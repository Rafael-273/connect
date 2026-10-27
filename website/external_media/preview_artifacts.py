"""Bounded, persistent caches for editor waveforms, B-roll photos and noise auditions."""
import hashlib
import json
import logging
import math
import struct
import uuid
import wave
from datetime import timedelta
from threading import Thread

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone

from .ffmpeg_runner import FFmpegRunner

logger = logging.getLogger(__name__)
CACHE_VERSION = 1


class PreviewArtifactRunner(FFmpegRunner):
    def run_capture(self, command, *, timeout=None):
        return super().run_capture(command, timeout=min(timeout or 300, 300))


def artifact_key(kind, source, options):
    payload = json.dumps([CACHE_VERSION, kind, source, options], sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def wav_peaks(path, buckets=4096):
    """Read bounded PCM buckets; never retain decoded audio for the whole file."""
    with wave.open(str(path), 'rb') as audio:
        count, rate = audio.getnframes(), audio.getframerate()
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError('A forma de onda requer PCM mono de 16 bits.')
        step = max(1, math.ceil(count / buckets))
        peaks = []
        for _ in range(buckets):
            remaining, peak = step, 0
            while remaining:
                size = min(4096, remaining)
                data = audio.readframes(size)
                if not data:
                    break
                peak = max(peak, max((abs(value[0]) for value in struct.iter_unpack('<h', data)), default=0))
                remaining -= len(data) // 2
            peaks.append(round(peak / 32768, 5))
        return {'schema': 'connect.waveform.v1', 'durationMs': count / rate * 1000, 'peaks': peaks}


def render_artifact(kind, source, options, workspace, runner):
    from .ffmpeg_runner import FFmpegRunner
    if kind == 'waveform':
        pcm = workspace.file('audio', 'waveform.wav')
        runner.run([settings.FFMPEG_BINARY, '-y', '-threads', '1', '-i', FFmpegRunner.input_arg(source),
            '-vn', '-ac', '1', '-ar', '8000', '-c:a', 'pcm_s16le', str(pcm)], timeout=300)
        output = workspace.file('output', 'waveform.json')
        output.write_text(json.dumps(wav_peaks(pcm)), encoding='utf-8')
        return output
    if kind == 'broll_thumbnail':
        from .thumbnails import render_thumbnail
        output = workspace.file('frames', 'thumbnail.jpg')
        render_thumbnail(source, output, runner)
        return output
    if kind == 'noise_comparison':
        from .audio_noise import AudioCleanupService, NoiseReductionDecision
        output = workspace.file('audio', 'comparison.m4a')
        AudioCleanupService(runner=runner).render_preview_clip(source, output,
            NoiseReductionDecision.from_dict(options['decision']), variant=options['variant'])
        return output
    raise ValueError('Tipo de cache de preview inválido.')


class PreviewArtifactService:
    @classmethod
    def request(cls, project, kind, field, *, version='', options=None):
        from ..models.external_media import ProjectPreviewArtifact
        source = {'name': field.name, 'version': str(version)}
        options = options or {}
        key = artifact_key(kind, source, options)
        ready = ProjectPreviewArtifact.objects.filter(project=project, key=key, status='READY').first()
        if ready and ready.status == 'READY' and ready.file:
            return ready
        with transaction.atomic():
            artifact, created = ProjectPreviewArtifact.objects.get_or_create(project=project, key=key,
                defaults={'kind': kind, 'source': source, 'options': options, 'requested_at': timezone.now()})
            artifact = ProjectPreviewArtifact.objects.select_for_update().get(pk=artifact.pk)
            if artifact.status == 'READY' and artifact.file:
                return artifact
            if not created and artifact.requested_at > timezone.now() - timedelta(minutes=10):
                return artifact
            artifact.lease = uuid.uuid4().hex
            artifact.status = 'PENDING'
            artifact.error = ''
            artifact.requested_at = timezone.now()
            artifact.save(update_fields=['lease', 'status', 'error', 'requested_at'])
            transaction.on_commit(lambda: cls.enqueue(artifact.pk, artifact.lease))
            return artifact

    @staticmethod
    def enqueue(artifact_id, lease):
        def dispatch():
            try:
                from .tasks import create_preview_artifact
                create_preview_artifact.apply_async(args=(artifact_id, lease), queue='media_previews', retry=False, ignore_result=True)
            except Exception:
                logger.warning('Falha ao agendar cache de preview %s.', artifact_id, exc_info=True)
        Thread(target=dispatch, name=f'preview-artifact-{artifact_id}', daemon=True).start()

    @staticmethod
    def pending_response(artifact):
        response = JsonResponse({'status': artifact.status, 'retry_after': 5}, status=202, headers={'Cache-Control': 'no-store', 'Retry-After': '5'})
        return response

    @staticmethod
    def build(artifact_id, lease):
        from ..models.external_media import ProjectPreviewArtifact, get_external_media_storage
        from .ffmpeg_runner import FFmpegRunner
        from .media_input import StoredMediaFile, media_input_factory
        from .workspace import JobWorkspace
        with transaction.atomic():
            artifact = ProjectPreviewArtifact.objects.select_for_update().filter(pk=artifact_id).first()
            if not artifact or artifact.lease != lease or artifact.status != 'PENDING':
                return
            artifact.status = 'PROCESSING'
            artifact.requested_at = timezone.now()
            artifact.save(update_fields=['status', 'requested_at'])
        storage = get_external_media_storage()
        name = None
        try:
            field = StoredMediaFile(storage, artifact.source['name'])
            source = media_input_factory(field).get_ffmpeg_input()
            with JobWorkspace(artifact.project_id, f'preview-cache-{artifact.pk}', estimated_bytes=32 * 1024 * 1024) as workspace:
                output = render_artifact(artifact.kind, source, artifact.options, workspace, PreviewArtifactRunner())
                with output.open('rb') as handle:
                    name = storage.save(f'external_media/preview_artifacts/{artifact.project_id}/{artifact.key}{output.suffix}', File(handle))
            updated = ProjectPreviewArtifact.objects.filter(pk=artifact_id, lease=lease, status='PROCESSING').update(status='READY', file=name, error='')
            if not updated:
                storage.delete(name)
        except Exception as error:
            if name:
                storage.delete(name)
            ProjectPreviewArtifact.objects.filter(pk=artifact_id, lease=lease).update(status='ERROR', error=str(error)[:255])
            logger.warning('Falha ao preparar cache de preview %s.', artifact_id, exc_info=True)
