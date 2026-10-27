"""Persistent, versioned timeline thumbnails, generated only by preview workers."""
import hashlib
import logging
import time
import uuid
from threading import Thread

from django.conf import settings
from django.core.files import File
from django.db import transaction

logger = logging.getLogger(__name__)


def thumbnail_version(proxy):
    identity = f'{proxy.proxy_file.name}:{proxy.update_at.isoformat()}'
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def ready_thumbnail(proxy):
    item = (proxy.metadata or {}).get('timeline_thumbnail') or {}
    return item.get('name') if item.get('version') == thumbnail_version(proxy) and item.get('status') == 'READY' else None


def render_thumbnail(source, output, runner):
    from .ffmpeg_runner import FFmpegRunner
    runner.run([
        settings.FFMPEG_BINARY, '-y', '-threads', '1', '-ss', '0',
        '-i', FFmpegRunner.input_arg(source), '-an', '-frames:v', '1',
        '-vf', 'scale=240:135:force_original_aspect_ratio=decrease,format=yuv444p,pad=240:135:(ow-iw)/2:(oh-ih)/2,setsar=1',
        '-filter_threads', '1', '-threads', '1', '-q:v', '3', str(output),
    ], timeout=60)


class TimelineThumbnailService:
    @classmethod
    def request(cls, proxy):
        from ..models.external_media import ProjectSourceProxy
        with transaction.atomic():
            current = ProjectSourceProxy.objects.select_for_update().get(pk=proxy.pk)
            if ready_thumbnail(current):
                return
            previous = (current.metadata or {}).get('timeline_thumbnail') or {}
            version = thumbnail_version(current)
            if previous.get('version') == version and time.time() - previous.get('requested_at', 0) < 300:
                return
            token = uuid.uuid4().hex
            metadata = {**(current.metadata or {}), 'timeline_thumbnail': {
                'status': 'PENDING', 'version': version, 'token': token, 'requested_at': time.time(),
            }}
            # A thumbnail update must not change the video URL's update_at.
            ProjectSourceProxy.objects.filter(pk=current.pk).update(metadata=metadata)
            transaction.on_commit(lambda: cls.enqueue(current.pk, token))

    @staticmethod
    def enqueue(proxy_id, token):
        def dispatch():
            try:
                from .tasks import create_timeline_thumbnail
                create_timeline_thumbnail.apply_async(args=(proxy_id, token), queue='media_previews', retry=False, ignore_result=True)
            except Exception:
                logger.warning('Não foi possível agendar miniatura do proxy %s.', proxy_id, exc_info=True)
        Thread(target=dispatch, name=f'thumbnail-{proxy_id}', daemon=True).start()

    @staticmethod
    def build(proxy_id, token):
        from ..models.external_media import ProjectSourceProxy
        from .ffmpeg_runner import FFmpegRunner
        from .media_input import media_input_factory
        from .workspace import JobWorkspace
        with transaction.atomic():
            proxy = ProjectSourceProxy.objects.select_for_update().filter(pk=proxy_id, status=ProjectSourceProxy.Status.READY).first()
            if not proxy or not proxy.proxy_file:
                return
            job = (proxy.metadata or {}).get('timeline_thumbnail') or {}
            version = thumbnail_version(proxy)
            if job.get('token') != token or job.get('version') != version or job.get('status') != 'PENDING':
                return
            job = {**job, 'status': 'PROCESSING'}
            ProjectSourceProxy.objects.filter(pk=proxy_id).update(metadata={**(proxy.metadata or {}), 'timeline_thumbnail': job})
        storage = proxy.proxy_file.storage
        name = None
        try:
            source = media_input_factory(proxy.proxy_file)
            with JobWorkspace(proxy.project_id, f'timeline-thumbnail-{proxy.pk}', estimated_bytes=1024 * 1024) as workspace:
                output = workspace.file('frames', 'thumbnail.jpg')
                render_thumbnail(source.get_ffmpeg_input(), output, FFmpegRunner())
                with output.open('rb') as handle:
                    name = storage.save(f'{proxy.proxy_file.name}.thumbnail-{version}.jpg', File(handle))
            with transaction.atomic():
                current = ProjectSourceProxy.objects.select_for_update().get(pk=proxy_id)
                latest = (current.metadata or {}).get('timeline_thumbnail') or {}
                if latest.get('token') != token or thumbnail_version(current) != version:
                    storage.delete(name)
                    return
                metadata = {**(current.metadata or {}), 'timeline_thumbnail': {
                    **job, 'status': 'READY', 'name': name,
                }}
                ProjectSourceProxy.objects.filter(pk=proxy_id).update(metadata=metadata)
        except Exception:
            if name:
                storage.delete(name)
            logger.warning('Falha ao gerar miniatura do proxy %s.', proxy_id, exc_info=True)
