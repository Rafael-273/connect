"""Delivery configuration, integrity-checked uploads and editor file reuse."""
import base64
import hashlib
import hmac
import re
import uuid
from datetime import timedelta
from pathlib import PurePosixPath

from django.conf import settings
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, OuterRef, Q, Subquery
from django.utils import timezone

from website.external_media.s3_upload import S3DirectUpload
from website.models import ContentFile, MediaDelivery, MediaDeliveryRequirement, MediaTask, MediaContent

# The legacy "video" code stays valid for requirements already saved.
FORMATS = [('feed', 'Feed'), ('story', 'Story'), ('telao', 'Telão'),
           ('video', 'Vídeo'), ('reels', 'Reels'), ('horizontal', 'Horizontal'), ('other', 'Outro')]
VIDEO_TYPES = {'video', 'reel', 'youtube', 'livestream'}
GUIDANCE = {'story': {'guidance': '9:16 · 1080 × 1920 recomendado'},
            'reels': {'guidance': 'Vídeo vertical · 9:16 · 1080 × 1920 recomendado'},
            'feed': {'guidance': '1080 × 1080 ou 1080 × 1350 recomendado'},
            'horizontal': {'guidance': '16:9 · 1920 × 1080 recomendado'}}
MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.webp': 'image/webp', '.gif': 'image/gif', '.pdf': 'application/pdf',
        '.psd': 'image/vnd.adobe.photoshop', '.mp4': 'video/mp4',
        '.mov': 'video/quicktime', '.webm': 'video/webm'}
MESSAGE = 'Envie todos os arquivos obrigatórios antes de concluir esta etapa.'


def validate_file(name, size, mime, task):
    suffix = PurePosixPath(name).suffix.lower()
    expected = MIME.get(suffix)
    maximum = getattr(settings, 'MEDIA_DELIVERY_MAX_UPLOAD_MB', 2048) * 1024 * 1024
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= maximum:
        raise ValidationError(f'O arquivo deve ter entre 1 byte e {maximum // (1024 * 1024)} MB.')
    if not expected or mime not in (expected, '', 'application/octet-stream'):
        raise ValidationError('Tipo de arquivo não permitido ou incompatível com a extensão.')
    if task.content.content_type in VIDEO_TYPES and not expected.startswith('video/'):
        raise ValidationError('Esta etapa exige um arquivo de vídeo.')
    if task.content.content_type not in VIDEO_TYPES and expected.startswith('video/'):
        raise ValidationError('Selecione uma imagem, PDF ou PSD para esta etapa.')
    return expected


def sniff(data, mime):
    checks = {
        'image/png': data.startswith(b'\x89PNG\r\n\x1a\n'),
        'image/jpeg': data.startswith(b'\xff\xd8\xff'),
        'image/gif': data.startswith((b'GIF87a', b'GIF89a')),
        'image/webp': data.startswith(b'RIFF') and data[8:12] == b'WEBP',
        'application/pdf': data.startswith(b'%PDF-'),
        'image/vnd.adobe.photoshop': data.startswith(b'8BPS\x00\x01'),
        'video/mp4': data[4:8] == b'ftyp',
        'video/quicktime': data[4:8] in (b'ftyp', b'moov', b'wide', b'mdat'),
        'video/webm': data.startswith(b'\x1a\x45\xdf\xa3') and b'webm' in data[:4096],
    }
    if not checks.get(mime):
        raise ValidationError('O conteúdo do arquivo não corresponde ao tipo informado.')


def missing_requirements(task):
    deliveries = MediaDelivery.objects.filter(requirement_id=OuterRef('pk')).order_by('-version')
    return task.delivery_requirements.filter(active=True, required=True).annotate(
        current_status=Subquery(deliveries.values('status')[:1]),
        current_ready=Subquery(deliveries.values('asset__ready')[:1]),
    ).filter(Q(current_status__isnull=True) | Q(current_status='withdrawn') | Q(current_ready=False))


def assert_complete(task):
    if task.pk and missing_requirements(task).exists():
        raise ValidationError(MESSAGE)


def requirements_for(task):
    return task.delivery_requirements.filter(active=True).prefetch_related('deliveries__asset', 'deliveries__submitted_by', 'deliveries__reviews')


@transaction.atomic
def configure_requirements(task, formats, other=''):
    # Same lock order as completion/upload; changes never erase delivery history.
    MediaContent.objects.select_for_update().get(pk=task.content_id)
    task = MediaTask.objects.select_for_update().get(pk=task.pk)
    values = set(formats)
    if values - dict(FORMATS).keys():
        raise ValidationError('Formato de entrega inválido.')
    if 'other' in values and not other.strip():
        raise ValidationError('Informe o nome do formato Outro.')
    if len(other.strip()) > 100:
        raise ValidationError('O nome do formato deve ter no máximo 100 caracteres.')
    existing = list(task.delivery_requirements.all())
    previous_active = {r.pk for r in existing if r.active}
    keep = []
    for code, label in FORMATS:
        if code not in values:
            continue
        label = other.strip() if code == 'other' else label
        req = next((r for r in existing if r.format == code and r.label == label), None)
        if req is None:
            req = MediaDeliveryRequirement.objects.create(task=task, format=code, label=label, metadata=GUIDANCE.get(code, {}))
        elif not req.active:
            req.active = True
            req.save(update_fields=['active', 'update_at'])
        keep.append(req.pk)
    task.delivery_requirements.exclude(pk__in=keep).update(active=False)
    if task.status in ('in_review', 'in_progress') and previous_active != set(keep):
        if task.delivery_requirements.filter(active=True, required=True).exists():
            from website.services.delivery_reviews import update_review_progress
            update_review_progress(task)
        elif task.status == 'in_review':
            task.status = 'in_progress'
            task.save(update_fields=['status', 'update_at'])
            from website.services.demands_hub import sync_content_status_from_tasks
            sync_content_status_from_tasks(task.content)
    if task.status == 'completed' and missing_requirements(task).exists():
        task.status = 'pending'
        task.save(update_fields=['status', 'update_at'])


def upload_ticket(asset, user, requirement):
    return signing.dumps({'asset': str(asset.pk), 'user': user.pk, 'requirement': requirement.pk,
                          'token': str(asset.upload_token)}, salt='delivery-upload')


def ticket_asset(ticket, user, requirement):
    try:
        payload = signing.loads(ticket, salt='delivery-upload', max_age=86400)
        asset = ContentFile.objects.get(pk=payload['asset'])
        if payload['user'] != user.pk or payload['requirement'] != requirement.pk or payload['token'] != str(asset.upload_token):
            raise PermissionDenied
        return asset
    except (signing.BadSignature, ContentFile.DoesNotExist, KeyError, ValueError):
        raise ValidationError('O upload expirou. Selecione o arquivo novamente.')


@transaction.atomic
def begin_upload(requirement, user, name, size, mime, sha256):
    mime = validate_file(name, size, mime, requirement.task)
    if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise ValidationError('SHA-256 inválido.')
    # Unique SHA constraint arbitrates simultaneous requests before any S3 upload.
    asset, created = ContentFile.objects.get_or_create(sha256=sha256, defaults={
        'file': f'contents/{uuid.uuid4().hex}{PurePosixPath(name).suffix.lower()}',
        'size': size, 'mime_type': mime, 'original_name': PurePosixPath(name.replace('\\', '/')).name[:255],
        'created_by': user, 'upload_expires_at': timezone.now() + timedelta(hours=2),
    })
    asset = ContentFile.objects.select_for_update().get(pk=asset.pk)
    if asset.size != size or asset.mime_type != mime:
        raise ValidationError('O arquivo não corresponde ao hash informado.')
    if not asset.ready and not created:
        if asset.upload_expires_at and asset.upload_expires_at > timezone.now() and asset.created_by_id != user.pk:
            raise ValidationError('Um envio deste arquivo está em andamento. Tente novamente em instantes.')
        if not asset.upload_expires_at or asset.upload_expires_at <= timezone.now():
            asset.upload_token = uuid.uuid4()
            asset.created_by = user
            asset.upload_expires_at = timezone.now() + timedelta(hours=2)
            asset.save(update_fields=['upload_token', 'created_by', 'upload_expires_at'])
    result = {'ticket': upload_ticket(asset, user, requirement), 'existing': asset.ready}
    if asset.ready:
        return result
    storage = asset.storage
    if hasattr(storage, 'bucket_name'):
        checksum = base64.b64encode(bytes.fromhex(sha256)).decode()
        direct = S3DirectUpload(storage)
        # Single PUT supports up to 5 GiB. Our configurable limit is capped below it.
        if size > 5 * 1024 ** 3:
            raise ValidationError('O limite deste envio é 5 GB.')
        result['upload_url'] = direct.client.generate_presigned_url('put_object', Params={
            **direct.target(asset.file.name), 'ContentType': mime, 'ContentLength': size,
            'ChecksumSHA256': checksum,
        }, ExpiresIn=7200)
        result['headers'] = {'Content-Type': mime, 'x-amz-checksum-sha256': checksum}
    else:
        result['local'] = True
    return result


def verify_remote(asset):
    direct = S3DirectUpload(asset.storage)
    head = direct.client.head_object(**direct.target(asset.file.name), ChecksumMode='ENABLED')
    expected = base64.b64encode(bytes.fromhex(asset.sha256)).decode()
    if head.get('ContentLength') != asset.size or not hmac.compare_digest(head.get('ChecksumSHA256', ''), expected):
        raise ValidationError('O tamanho ou checksum do arquivo não corresponde ao envio.')
    # Read only the header, never buffer or relay the video through Django.
    response = direct.client.get_object(**direct.target(asset.file.name), Range='bytes=0-4095')
    try:
        sniff(response['Body'].read(4096), asset.mime_type)
    finally:
        response['Body'].close()
    asset.ready = True
    asset.save(update_fields=['ready'])


def save_local(asset, upload):
    if upload is None or upload.size != asset.size:
        raise ValidationError('Arquivo ausente ou tamanho incorreto.')
    digest = hashlib.sha256()
    header = b''
    for chunk in upload.chunks():
        digest.update(chunk)
        if len(header) < 4096:
            header += chunk[:4096 - len(header)]
    if not hmac.compare_digest(digest.hexdigest(), asset.sha256):
        raise ValidationError('O checksum do arquivo não corresponde ao envio.')
    sniff(header, asset.mime_type)
    upload.seek(0)
    asset.file.save(asset.file.name, upload, save=False)
    asset.ready = True
    asset.save(update_fields=['file', 'ready'])


@transaction.atomic
def deliver(requirement, asset, user, submission_id, name='', metadata=None):
    MediaContent.objects.select_for_update().get(pk=requirement.task.content_id)
    task = MediaTask.objects.select_for_update().get(pk=requirement.task_id)
    requirement = MediaDeliveryRequirement.objects.select_for_update().get(pk=requirement.pk)
    if task.assigned_to_id != user.pk or not requirement.active or task.deleted or task.content.deleted:
        raise PermissionDenied
    if not asset.ready:
        raise ValidationError('O arquivo ainda não está disponível.')
    validate_file(name or asset.original_name, asset.size, asset.mime_type, task)
    previous = MediaDelivery.objects.filter(submission_id=submission_id).first()
    if previous:
        if previous.requirement_id != requirement.pk or previous.asset_id != asset.pk or previous.submitted_by_id != user.pk:
            raise ValidationError('Identificação de envio já utilizada.')
        return previous
    version = (MediaDelivery.all_objects.filter(requirement=requirement).aggregate(value=Max('version'))['value'] or 0) + 1
    if task.status == 'in_review':
        raise ValidationError('As entregas estão em revisão. Aguarde a avaliação da liderança.')
    from website.services.delivery_reviews import update_review_progress
    requirements = task.delivery_requirements.filter(active=True).prefetch_related('deliveries')
    current = [next(iter(item.deliveries.all()), None) for item in requirements]
    awaiting_correction = any(item and (item.status in ('rejected', 'draft') or
        (item.status == 'withdrawn' and item.metadata.get('withdrawn_requires_confirmation'))) for item in current)
    delivery = MediaDelivery.objects.create(requirement=requirement, asset=asset, version=version,
        submitted_by=user, submission_id=submission_id,
        original_name=PurePosixPath((name or asset.original_name).replace('\\', '/')).name[:255],
        status='draft' if awaiting_correction else 'submitted', metadata=metadata or {})
    update_review_progress(task, on_submission=True)
    return delivery


@transaction.atomic
def withdraw_delivery(requirement, user, delivery_id):
    """Retire the current version without deleting its file or audit history."""
    content = MediaContent.objects.select_for_update().get(pk=requirement.task.content_id)
    task = MediaTask.objects.select_for_update().get(pk=requirement.task_id, content=content)
    requirement = MediaDeliveryRequirement.objects.select_for_update().get(pk=requirement.pk, task=task)
    if task.assigned_to_id != user.pk or not requirement.active or task.deleted or content.deleted:
        raise PermissionDenied
    delivery = requirement.deliveries.select_for_update().first()
    if not delivery or str(delivery.pk) != str(delivery_id) or delivery.status == 'withdrawn':
        raise ValidationError('Esta entrega foi alterada. Atualize a página e tente novamente.')
    old_status = delivery.status
    delivery.metadata = {**delivery.metadata,
        'withdrawn_previous_status': old_status,
        'withdrawn_by': user.pk,
        'withdrawn_at': timezone.now().isoformat(),
        'withdrawn_requires_confirmation': task.status in ('in_review', 'completed') or old_status in ('rejected', 'draft')}
    delivery.status = 'withdrawn'
    delivery.save(update_fields=['status', 'metadata', 'update_at'])
    from website.services.delivery_reviews import update_review_progress
    update_review_progress(task)
    if task.status == 'in_review' and not task.delivery_requirements.filter(active=True, required=True).exists():
        task.status = 'in_progress'
        task.save(update_fields=['status', 'update_at'])
        from website.services.demands_hub import sync_content_status_from_tasks
        sync_content_status_from_tasks(content)
    return task


def register_editor_file(source, user):
    """Keep an immutable reference to the current physical render, never the project."""
    if not source.file or source.kind != 'VIDEO':
        raise ValidationError('Ainda não há uma exportação disponível para este projeto.')
    if source.content_file_id and source.content_file.ready:
        return source.content_file
    storage = source.file.storage
    if not storage.exists(source.file.name):
        raise ValidationError('O arquivo desta exportação não está disponível.')
    asset, _ = ContentFile.objects.get_or_create(file=source.file.name, defaults={
        'size': source.file_size or source.file.size, 'mime_type': 'video/mp4',
        'original_name': PurePosixPath(source.file.name).name, 'origin': 'video_editor',
        'created_by': user, 'ready': True,
        'metadata': {'editor_asset_id': source.pk, 'job_id': source.job_id},
    })
    return asset


def is_retained_file(name):
    return bool(name) and ContentFile.objects.filter(file=name, ready=True).exists()


def index_render_file(source, source_path=None):
    """Index a final video in the render worker, where hashing won't block HTTP."""
    if source.kind != 'VIDEO':
        return None
    if source_path is None:
        context = source.file.storage.open(source.file.name, 'rb')
    elif hasattr(source_path, 'storage_name'):
        from django.core.files.storage import storages
        context = storages['external_media'].open(source_path.storage_name, 'rb')
    else:
        context = source_path.open('rb')
    digest = hashlib.sha256()
    with context as stream:
        while True:
            chunk = stream.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    with transaction.atomic():
        # The unique constraint, not an earlier lookup, arbitrates equal renders.
        asset = ContentFile.objects.select_for_update().filter(file=source.file.name).first()
        if asset is None:
            asset, _ = ContentFile.objects.get_or_create(sha256=digest.hexdigest(), defaults={
                'file': source.file.name, 'size': source.file_size,
                'mime_type': 'video/mp4', 'original_name': PurePosixPath(source.file.name).name,
                'origin': 'video_editor', 'created_by': source.job.created_by.user, 'ready': True,
                'metadata': {'editor_asset_id': source.pk, 'job_id': source.job_id},
            })
        if not asset.ready:
            # A manual reservation has no canonical object yet. Use the verified
            # render and invalidate its old upload ticket, without waiting on HTTP.
            asset = ContentFile.objects.select_for_update().get(pk=asset.pk)
            asset.file = source.file.name
            asset.origin = 'video_editor'
            asset.ready = True
            asset.upload_token = uuid.uuid4()
            asset.save(update_fields=['file', 'origin', 'ready', 'upload_token'])
        if asset.sha256 is None:
            from django.db import IntegrityError
            equivalent = None
            try:
                with transaction.atomic():
                    asset.sha256 = digest.hexdigest()
                    asset.save(update_fields=['sha256'])
            except IntegrityError:
                asset.sha256 = None
                equivalent = ContentFile.objects.select_for_update().get(sha256=digest.hexdigest())
            if equivalent is not None:
                if equivalent.ready:
                    asset = equivalent
                else:
                    # Discard only an unconfirmed reservation, never a delivered
                    # asset. Its signed ticket becomes invalid; retry reuses this
                    # already verified render instead of uploading it again.
                    from website.models import MediaAsset
                    MediaAsset.objects.filter(content_file=equivalent).update(content_file=asset)
                    equivalent.delete()
                    asset.sha256 = digest.hexdigest()
                    asset.save(update_fields=['sha256'])
        # Previous deliveries retain their original physical file identity.
        source.content_file = asset
        source.save(update_fields=['content_file', 'update_at'])
    return asset
