"""Background processing for ministry manuals."""
import json
import logging
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from website.ai import get_ai_service
from website.models import ManualGenerationJob, MinistryManual
from website.services.manual_generation import generate_manual

logger = logging.getLogger(__name__)
MAX_SECONDS = 2 * 60 * 60


def validate_audio(path):
    try:
        probe = subprocess.run(
            [settings.FFPROBE_BINARY, '-v', 'error', '-show_entries', 'format=duration:stream=codec_type',
             '-of', 'json', str(path)], capture_output=True, timeout=30, check=True,
        )
        info = json.loads(probe.stdout)
        duration = float(info['format']['duration'])
        if not 0 < duration <= MAX_SECONDS or not any(s.get('codec_type') == 'audio' for s in info['streams']):
            raise ValueError
    except (OSError, subprocess.SubprocessError, KeyError, ValueError, TypeError) as exc:
        raise ValueError('Áudio inválido ou com duração superior a 2 horas.') from exc


@shared_task(name='manuals.generate')
def generate_manual_job(job_id, run_token):
    with transaction.atomic():
        job = ManualGenerationJob.objects.select_for_update().filter(pk=job_id, status='waiting', run_token=run_token).first()
        if not job:
            return
        job.status = 'transcribing' if not job.transcript else 'generating'
        job.error = ''
        job.save(update_fields=['status', 'error', 'updated_at'])
    try:
        if not job.transcript:
            suffix = Path(job.filename).suffix.lower()
            with tempfile.NamedTemporaryFile(suffix=suffix) as audio:
                audio.write(bytes(job.audio))
                audio.flush()
                validate_audio(audio.name)
                transcript = get_ai_service().transcribe(audio.name, language='pt')
            if not ManualGenerationJob.objects.filter(pk=job_id, run_token=run_token).update(transcript=transcript, status='generating', updated_at=timezone.now()):
                return
            job.transcript = transcript
        generated = generate_manual(job.transcript)
        with transaction.atomic():
            job = ManualGenerationJob.objects.select_for_update().get(pk=job_id)
            if job.manual_id or str(job.run_token) != run_token:
                return
            manual = MinistryManual.objects.create(
                ministry=job.ministry, created_by=job.created_by, title=generated['title'],
                content=generated['content'], is_draft=True, origin='ai',
            )
            job.manual = manual
            job.status = 'completed'
            job.audio = None
            job.transcript = ''
            job.save(update_fields=['manual', 'status', 'audio', 'transcript', 'updated_at'])
    except Exception as exc:
        # Provider exceptions can include request data; do not log exception details.
        logger.error('Falha no processamento do manual, tarefa %s', job_id)
        error = str(exc) if isinstance(exc, ValueError) and str(exc) == 'Áudio inválido ou com duração superior a 2 horas.' else 'Não foi possível processar o áudio. Verifique o arquivo e tente novamente.'
        ManualGenerationJob.objects.filter(pk=job_id, run_token=run_token).update(
            status='error', error=error,
            updated_at=timezone.now(),
        )


@shared_task(name='manuals.cleanup')
def cleanup_manual_jobs():
    ManualGenerationJob.objects.filter(updated_at__lt=timezone.now() - timedelta(days=1)).delete()
