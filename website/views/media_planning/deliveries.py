"""Authenticated delivery actions; file access always follows the owning step."""
import json
import logging
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from website.models import MediaDelivery, MediaDeliveryRequirement, MediaTask, ExternalMediaProject, MediaAsset
from website.services import content_deliveries as service
from website.services.media_work import individual_tasks
from website.views.mixins import MemberRequiredMixin
from .mixins import _get_media_membership, _is_media_org_admin

logger = logging.getLogger(__name__)


def delivery_result(request, requirement, task, *, delivery=None, force_refresh=False):
    from django.template.loader import render_to_string
    from website.services.delivery_reviews import needs_confirmation
    task.refresh_from_db(fields=['status'])
    requirement = service.requirements_for(task).get(pk=requirement.pk)
    html = render_to_string('member/media_planning/partials/delivery_requirement.html', {
        'requirement': requirement, 'delivery_writable': task.status != 'in_review',
        'delivery_removable': True, 'delivery_integrated': True,
        'delivery_video': task.content.content_type in service.VIDEO_TYPES,
    }, request=request)
    return JsonResponse({'version': delivery.version if delivery else None,
        'name': delivery.original_name if delivery else '', 'html': html,
        'task_status': task.status, 'needs_confirmation': needs_confirmation(task),
        'force_refresh': force_refresh})


class DeliveryAccessMixin(MemberRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        member = getattr(request.user, 'member', None)
        membership = _get_media_membership(member) if member else None
        self.can_manage = _is_media_org_admin(request.user) or bool(membership and membership.role == 'leader')
        if not request.user.is_active or not (membership or self.can_manage):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def requirement(self, pk, write=False):
        tasks = MediaTask.objects.filter(content__deleted__isnull=True)
        if write or not self.can_manage:
            tasks = individual_tasks(self.request.user)
        return get_object_or_404(MediaDeliveryRequirement.objects.select_related('task__content').filter(
            task__in=tasks, **({'active': True} if write else {})), pk=pk)


class MediaDeliveryActionView(DeliveryAccessMixin, View):
    def post(self, request, pk):
        requirement = self.requirement(pk, write=True)
        try:
            data = request.POST if request.content_type != 'application/json' else json.loads(request.body)
            if not hasattr(data, 'get'):
                raise ValidationError('Dados de envio inválidos.')
            action = data.get('action')
            if action == 'withdraw':
                task = service.withdraw_delivery(requirement, request.user, data.get('delivery_id'))
                return delivery_result(request, requirement, task, force_refresh=True)
            if action == 'begin':
                if requirement.task.status == 'in_review':
                    raise ValidationError('As entregas estão em revisão. Aguarde a avaliação da liderança.')
                result = service.begin_upload(requirement, request.user, data.get('name', ''),
                    data.get('size'), data.get('mime', ''), data.get('sha256'))
                return JsonResponse(result)
            submission_id = uuid.UUID(data.get('submission_id', ''))
            if action == 'editor':
                # The current Editor grants all active ministry members project access.
                # Keep the same gate and prioritize the member's projects in the picker.
                source = get_object_or_404(editor_sources(), pk=data.get('source_id'))
                with transaction.atomic():
                    source = MediaAsset.objects.select_for_update().select_related('job').get(pk=source.pk)
                    asset = service.register_editor_file(source, request.user)
                    if not asset.sha256:
                        from django.utils import timezone
                        from website.services.delivery_file_jobs import enqueue_editor_index
                        source.content_file = asset
                        source.save(update_fields=['content_file', 'update_at'])
                        if asset.metadata.get('hash_error') and not data.get('retry_index'):
                            raise ValidationError('Não foi possível preparar o vídeo. Tente selecionar a exportação novamente.')
                        requested = asset.metadata.get('hash_requested_at', 0)
                        if not requested or timezone.now().timestamp() - requested > 600:
                            asset.metadata.pop('hash_error', None)
                            asset.metadata['hash_requested_at'] = timezone.now().timestamp()
                            asset.save(update_fields=['metadata'])
                            transaction.on_commit(lambda: enqueue_editor_index(source.pk))
                        return JsonResponse({'pending': True, 'message': 'Preparando o arquivo do Editor para reutilização…'}, status=202)
                    delivery = service.deliver(requirement, asset, request.user, submission_id,
                        metadata={'editor_asset_id': source.pk, 'job_id': source.job_id})
            elif action == 'complete':
                asset = service.ticket_asset(data.get('ticket', ''), request.user, requirement)
                with transaction.atomic():
                    # Serialize confirmations so local storage cannot create a second object.
                    asset = type(asset).objects.select_for_update().get(pk=asset.pk)
                    if not asset.ready:
                        if hasattr(asset.storage, 'bucket_name'):
                            service.verify_remote(asset)
                        else:
                            service.save_local(asset, request.FILES.get('file'))
                    delivery = service.deliver(requirement, asset, request.user, submission_id, data.get('name', ''))
            else:
                raise ValidationError('Ação inválida.')
            return delivery_result(request, requirement, delivery.requirement.task, delivery=delivery)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return JsonResponse({'error': ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'Dados de envio inválidos.'}, status=400)
        except (PermissionDenied, Http404):
            raise
        except Exception:
            logger.exception('delivery_upload_failed requirement=%s', requirement.pk)
            return JsonResponse({'error': 'Não foi possível concluir o envio. Tente novamente.'}, status=503)


def editor_sources():
    return MediaAsset.objects.filter(kind='VIDEO', job__status='FINISHED', job__deleted__isnull=True).filter(
        Q(job__project__deleted__isnull=True, job__project__isnull=False)
        | Q(job__processing_project__deleted__isnull=True, job__processing_project__isnull=False)
    ).exclude(file='').select_related('job__preset')


class MediaDeliveryProjectsView(DeliveryAccessMixin, View):
    def get(self, request, pk):
        requirement = self.requirement(pk, write=True)
        if requirement.task.content.content_type not in service.VIDEO_TYPES:
            raise PermissionDenied
        project_id = request.GET.get('project')
        if project_id:
            if not project_id.isascii() or not project_id.isdigit() or len(project_id) > 18:
                return JsonResponse({'error': 'Projeto inválido.'}, status=400)
            project = get_object_or_404(ExternalMediaProject, pk=project_id)
            sources = editor_sources().filter(Q(job_id=project.render_job_id) | Q(job__processing_project=project))
            return JsonResponse({'exports': [{'id': source.pk,
                'name': source.file.name.rsplit('/', 1)[-1], 'language': source.get_language_display(),
                'created_at': source.update_at.isoformat(), 'format': source.job.preset.name,
                'width': source.job.preset.width, 'height': source.job.preset.height,
            } for source in sources]})
        from django.db.models import Case, When, Value, IntegerField
        projects = ExternalMediaProject.objects.annotate(own=Case(
            When(created_by__user=request.user, then=Value(0)), default=Value(1), output_field=IntegerField(),
        )).select_related('created_by').order_by('own', '-created_at', '-pk')
        query = request.GET.get('q', '').strip()[:180]
        for term in query.split()[:10]:
            projects = projects.filter(Q(name__icontains=term) | Q(created_by__name__icontains=term))
        from django.core.paginator import Paginator
        page = Paginator(projects, 20).get_page(request.GET.get('page', 1))
        return JsonResponse({'projects': [{
            'id': p.pk, 'name': p.name, 'owner': p.created_by.name,
            'is_owner': p.own == 0, 'created_at': p.created_at.isoformat(),
        } for p in page], 'has_more': page.has_next(), 'page': page.number})


class MediaDeliveryFileView(DeliveryAccessMixin, View):
    def get(self, request, pk):
        delivery = get_object_or_404(MediaDelivery.objects.select_related('asset', 'requirement'), pk=pk, asset__ready=True)
        self.requirement(delivery.requirement_id)
        preview = request.GET.get('preview') == '1' and delivery.asset.mime_type in {
            'image/png', 'image/jpeg', 'image/gif', 'image/webp',
            'video/mp4', 'video/webm', 'video/quicktime',
        }
        storage = delivery.asset.storage
        if hasattr(storage, 'bucket_name'):
            direct = service.S3DirectUpload(storage)
            from django.utils.http import content_disposition_header
            response = redirect(direct.client.generate_presigned_url('get_object', Params={
                **direct.target(delivery.asset.file.name),
                'ResponseContentDisposition': content_disposition_header(not preview, delivery.original_name),
                'ResponseContentType': delivery.asset.mime_type,
            }, ExpiresIn=300))
        else:
            response = FileResponse(storage.open(delivery.asset.file.name, 'rb'), as_attachment=not preview,
                filename=delivery.original_name, content_type=delivery.asset.mime_type)
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
