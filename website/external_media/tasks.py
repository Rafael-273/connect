from decimal import Decimal
from contextlib import contextmanager
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.db import connection
from django.utils.text import slugify
from django.utils import timezone

from ..models.external_media import (
    ExternalMediaJob,
    ExternalMediaProject,
    ExternalMediaProjectExport,
    ProjectBlockMedia,
    SubtitleReviewEvent,
    SubtitleReviewSession,
    SubtitleTrack,
    SubtitleVideoVersion,
    SubtitleVideoVersionAsset,
    VideoMasteringJob,
)
from .audio_analysis import AudioAnalysisService
from .audio_mastering import AudioMasteringService, MasteringTarget
from .audio_muxing import AudioMuxingService
from .audio_validation import AudioValidationService
from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner
from .premiere_export import PremierePackageService
from .services import ExternalMediaPipeline, ExternalMediaProjectPipeline, VideoAssemblyService
from .subtitle_reviews import SubtitleReviewService
from .timeline import InternalTimelineBuilder
from .workspace import JobWorkspace, MediaWorkspaceGarbageCollector, estimate_media_workspace_bytes


@shared_task(bind=True, autoretry_for=(), name='external_media.prepare_subtitles')
def prepare_external_media(self, job_id):
    ExternalMediaPipeline().prepare_subtitles(job_id)


@shared_task(bind=True, autoretry_for=(), name='external_media.render_outputs')
def render_external_media(self, job_id):
    ExternalMediaPipeline().render_outputs(job_id)


@shared_task(name='external_media.cleanup_workspaces')
def cleanup_external_media_workspaces():
    """Safety net for scratch directories left by interrupted workers."""
    return MediaWorkspaceGarbageCollector.collect()


@shared_task(bind=True, autoretry_for=(), name='external_media.run_project')
def run_external_media_project(self, project_id):
    with _project_execution_lock(project_id) as acquired:
        if not acquired:
            return {'skipped': True, 'reason': 'project-already-processing'}
        if not _execution_is_current(project_id, self.request.id):
            return {'skipped': True, 'reason': 'stale-execution'}
        ExternalMediaProjectPipeline().run(project_id)


@shared_task(bind=True, autoretry_for=(), name='external_media.create_project_preview')
def create_project_preview(self, media_id):
    try:
        item = ProjectBlockMedia.objects.get(pk=media_id)
    except ProjectBlockMedia.DoesNotExist:
        # The upload may have been removed while its preview job was queued.
        return {'skipped': True, 'reason': 'media-deleted'}
    item.preview_status = ProjectBlockMedia.PreviewStatus.PENDING
    item.preview_error = ''
    item.save(update_fields=['preview_status', 'preview_error', 'update_at'])
    try:
        with JobWorkspace(
            item.pk,
            'upload-preview',
            estimated_bytes=estimate_media_workspace_bytes(getattr(item.file, 'size', 0), needs_proxy=True),
        ) as workspace:
            source = workspace.file('source', f'source{Path(item.file.name).suffix.lower()}')
            preview = workspace.file('proxy', 'preview.mp4')
            _local_file(item.file, source)
            VideoAssemblyService().create_proxy(source, preview)
            with preview.open('rb') as handle:
                item.preview_file.save('preview.mp4', File(handle), save=False)
        item.preview_status = ProjectBlockMedia.PreviewStatus.READY
        item.preview_error = ''
        item.save(update_fields=['preview_file', 'preview_status', 'preview_error', 'update_at'])
    except Exception as exc:
        item.preview_status = ProjectBlockMedia.PreviewStatus.ERROR
        item.preview_error = str(exc)[:255]
        item.save(update_fields=['preview_status', 'preview_error', 'update_at'])
        raise


@shared_task(bind=True, autoretry_for=(), name='external_media.create_subtitle_review_preview')
def create_subtitle_review_preview(self, session_id):
    session = SubtitleReviewSession.objects.select_related('job').get(pk=session_id)
    source_file = session.job.original_video
    with JobWorkspace(
        session.pk,
        'subtitle-review-preview',
        estimated_bytes=estimate_media_workspace_bytes(getattr(source_file, 'size', 0), needs_proxy=True),
    ) as workspace:
        source = workspace.file('source', f'source{Path(source_file.name).suffix.lower()}')
        preview = workspace.file('proxy', 'preview.mp4')
        _local_file(source_file, source)
        VideoAssemblyService().create_proxy(source, preview)
        with preview.open('rb') as handle:
            session.preview_file.save('preview.mp4', File(handle), save=False)
        session.save(update_fields=['preview_file', 'update_at'])


@shared_task(bind=True, autoretry_for=(), name='external_media.render_reviewed_subtitles')
def render_reviewed_subtitles(self, project_id, review_session_id=None):
    with _project_execution_lock(project_id) as acquired:
        if not acquired:
            return {'skipped': True, 'reason': 'project-already-processing'}
        if not _execution_is_current(project_id, self.request.id):
            return {'skipped': True, 'reason': 'stale-execution'}
        project = ExternalMediaProject.objects.select_related('render_job').get(pk=project_id)
        job = project.render_job
        if not job:
            raise ExternalMediaError('O projeto ainda não possui um vídeo preparado.')
        try:
            project.status = ExternalMediaProject.Status.PROCESSING
            project.progress = 86
            project.current_step = 'Aplicando as legendas revisadas'
            project.error_message = ''
            project.save(update_fields=[
                'status', 'progress', 'current_step', 'error_message', 'update_at',
            ])
            ExternalMediaPipeline().render_outputs(job.pk)
            SubtitleTrack.objects.filter(job=job).update(subtitle_dirty=False)
            session = SubtitleReviewSession.objects.filter(pk=review_session_id).first()
            video_version = SubtitleVideoVersion.objects.create(
                project=project,
                job=job,
                version=SubtitleReviewService.next_video_version(project),
                subtitle_revisions={
                    track.language: track.revision
                    for track in SubtitleTrack.objects.filter(job=job)
                },
                review_session=session,
            )
            for asset in job.assets.all():
                version_asset = SubtitleVideoVersionAsset(
                    version=video_version,
                    kind=asset.kind,
                    language=asset.language,
                    file_size=asset.file_size,
                )
                with asset.file.open('rb') as source:
                    version_asset.file.save(Path(asset.file.name).name, File(source), save=False)
                version_asset.save()
            project.status = ExternalMediaProject.Status.FINISHED
            project.progress = 100
            project.current_step = 'Vídeo atualizado'
            project.finished_at = timezone.now()
            project.save(update_fields=[
                'status', 'progress', 'current_step', 'finished_at', 'update_at',
            ])
            if session:
                SubtitleReviewEvent.objects.create(
                    session=session,
                    event='RENDER_COMPLETED',
                    details={'video_version': video_version.version},
                )
        except Exception as exc:
            project.status = ExternalMediaProject.Status.ERROR
            project.current_step = 'Não foi possível atualizar o vídeo'
            project.error_message = str(exc)
            project.save(update_fields=[
                'status', 'current_step', 'error_message', 'update_at',
            ])
            raise


@shared_task(bind=True, autoretry_for=(), name='external_media.render_project')
def render_external_media_project(self, project_id):
    with _project_execution_lock(project_id) as acquired:
        if not acquired:
            return {'skipped': True, 'reason': 'project-already-processing'}
        if not _execution_is_current(project_id, self.request.id):
            return {'skipped': True, 'reason': 'stale-execution'}
        ExternalMediaProjectPipeline().render(project_id)


@contextmanager
def _project_execution_lock(project_id):
    """Serialize a project across workers; PostgreSQL releases this lock on crashes."""
    if connection.vendor != 'postgresql':
        yield True
        return
    namespace = settings.EXTERNAL_MEDIA_TASK_LOCK_NAMESPACE
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', [namespace, int(project_id)])
        acquired = bool(cursor.fetchone()[0])
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s, %s)', [namespace, int(project_id)])


def _execution_is_current(project_id, task_id):
    project = ExternalMediaProject.objects.only('status', 'celery_task_id').get(pk=project_id)
    if task_id and project.celery_task_id and str(task_id) != project.celery_task_id:
        return False
    return project.status not in {
        ExternalMediaProject.Status.FINISHED,
        ExternalMediaProject.Status.CANCELLED,
    }


def _mastering_failure(job_id, exc):
    VideoMasteringJob.objects.filter(pk=job_id).update(
        status=VideoMasteringJob.Status.ERROR,
        error_message=str(exc) or 'Não foi possível processar o áudio deste vídeo.',
        current_step='Processamento interrompido',
        finished_at=timezone.now(),
    )


def _local_file(field_file, destination):
    with field_file.open('rb') as source, destination.open('wb') as target:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            target.write(chunk)


def _extract_audio(runner, video_path, audio_path):
    runner.run([
        settings.FFMPEG_BINARY, '-y', '-i', str(video_path), '-vn',
        '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s24le', str(audio_path),
    ])


@shared_task(bind=True, autoretry_for=(), name='external_media.analyze_video_mastering')
def analyze_video_mastering(self, job_id):
    try:
        job = VideoMasteringJob.objects.get(pk=job_id)
        job.status = VideoMasteringJob.Status.ANALYZING
        job.progress = 10
        job.current_step = 'Extraindo e analisando o áudio'
        job.started_at = job.started_at or timezone.now()
        job.error_message = ''
        job.save(update_fields=['status', 'progress', 'current_step', 'started_at', 'error_message', 'update_at'])
        with JobWorkspace(
            job.public_id,
            'mastering-analysis',
            estimated_bytes=estimate_media_workspace_bytes(getattr(job.original_video, 'size', 0)),
        ) as workspace:
            source = workspace.file('source', f'original{Path(job.original_video.name).suffix.lower()}')
            audio = workspace.file('audio', 'audio_extracted.wav')
            _local_file(job.original_video, source)
            runner = FFmpegRunner()
            _extract_audio(runner, source, audio)
            metrics = AudioAnalysisService(runner).analyze(audio)
        job.input_metrics = metrics
        job.input_lufs = _decimal_or_none(metrics.get('integrated_lufs'))
        job.input_true_peak = _decimal_or_none(metrics.get('true_peak_dbtp'))
        job.status = VideoMasteringJob.Status.READY
        job.progress = 25
        job.current_step = 'Análise concluída. Selecione um perfil.'
        job.save(update_fields=[
            'input_metrics', 'input_lufs', 'input_true_peak', 'status', 'progress',
            'current_step', 'update_at',
        ])
    except Exception as exc:
        _mastering_failure(job_id, exc)
        raise


@shared_task(bind=True, autoretry_for=(), name='external_media.master_video')
def master_video(self, job_id):
    try:
        job = VideoMasteringJob.objects.select_related('mastering_profile').get(pk=job_id)
        if not job.mastering_profile:
            raise ValueError('Selecione um perfil de masterização.')
        profile = job.mastering_profile
        job.status = VideoMasteringJob.Status.MASTERING
        job.progress = 38
        job.current_step = 'Masterizando o mix final'
        job.started_at = timezone.now()
        job.finished_at = None
        job.error_message = ''
        job.save(update_fields=['status', 'progress', 'current_step', 'started_at', 'finished_at', 'error_message', 'update_at'])
        with JobWorkspace(
            job.public_id,
            'mastering',
            estimated_bytes=estimate_media_workspace_bytes(getattr(job.original_video, 'size', 0)),
        ) as workspace:
            source = workspace.file('source', f'original{Path(job.original_video.name).suffix.lower()}')
            extracted = workspace.file('audio', 'audio_extracted.wav')
            mastered = workspace.file('audio', 'audio_mastered.wav')
            output = workspace.file('output', 'video_mastered.mp4')
            _local_file(job.original_video, source)
            runner = FFmpegRunner()
            _extract_audio(runner, source, extracted)
            result = AudioMasteringService(runner).master_audio(
                extracted, mastered, MasteringTarget.from_profile(profile),
            )
            VideoMasteringJob.objects.filter(pk=job.pk).update(
                status=VideoMasteringJob.Status.VALIDATING,
                progress=70,
                current_step='Validando loudness e true peak',
            )
            output_metrics = AudioValidationService(AudioAnalysisService(runner)).validate(mastered, profile)
            if not output_metrics.get('integrity_ok'):
                raise ExternalMediaError('O áudio masterizado não passou na validação de integridade.')
            if not output_metrics.get('true_peak_compliant'):
                raise ExternalMediaError('O resultado excedeu o limite de True Peak do perfil.')
            VideoMasteringJob.objects.filter(pk=job.pk).update(
                status=VideoMasteringJob.Status.MUXING,
                progress=86,
                current_step='Substituindo o áudio sem recomprimir a imagem',
            )
            mux_result = AudioMuxingService(runner).mux(source, mastered, output)
            if job.output_video:
                job.output_video.delete(save=False)
            with output.open('rb') as handle:
                job.output_video.save('mastered.mp4', File(handle), save=False)
        master_metrics = result.metrics
        job.output_metrics = output_metrics
        job.output_lufs = _decimal_or_none(output_metrics.get('integrated_lufs'))
        job.output_true_peak = _decimal_or_none(output_metrics.get('true_peak_dbtp'))
        job.gain_applied = _decimal_or_none(master_metrics.get('gain_applied_db'))
        job.limiter_gain_reduction = _decimal_or_none(master_metrics.get('limiter_gain_reduction_db'))
        job.video_reencoded = mux_result.video_reencoded
        job.status = VideoMasteringJob.Status.FINISHED
        job.progress = 100
        job.current_step = 'Masterização concluída'
        job.finished_at = timezone.now()
        job.save(update_fields=[
            'output_video', 'output_metrics', 'output_lufs', 'output_true_peak',
            'gain_applied', 'limiter_gain_reduction',
            'video_reencoded', 'status', 'progress', 'current_step', 'finished_at', 'update_at',
        ])
    except Exception as exc:
        _mastering_failure(job_id, exc)
        raise


def _decimal_or_none(value):
    return Decimal(str(value)) if value is not None else None


def _premiere_archive_filename(project):
    stem = slugify(project.name)[:120]
    if not stem:
        stem = f'projeto-{str(project.public_id).split("-")[0]}'
    return f'{stem}-premiere.zip'


@shared_task(bind=True, autoretry_for=(), name='external_media.export_premiere_project')
def export_premiere_project(self, export_id):
    export = ExternalMediaProjectExport.objects.select_related(
        'project__created_by',
        'project__template_version__template',
        'project__template_version__preset',
        'project__template_version__background_music',
        'project__template_version__mastering_profile',
        'project__render_job__subtitle_style',
        'project__render_job__translated_subtitle_style',
    ).get(pk=export_id)

    def progress(status, percent, label):
        ExternalMediaProjectExport.objects.filter(pk=export.pk).update(
            status=status, progress=percent, current_step=label,
        )

    try:
        export.status = ExternalMediaProjectExport.Status.PREPARING
        export.progress = 5
        export.current_step = 'Preparando exportação editável'
        export.started_at = timezone.now()
        export.finished_at = None
        export.error_message = ''
        export.save(update_fields=[
            'status', 'progress', 'current_step', 'started_at', 'finished_at',
            'error_message', 'update_at',
        ])
        with JobWorkspace(export.public_id, 'premiere-export') as workspace:
            package_root = workspace.path / 'package'
            archive_filename = _premiere_archive_filename(export.project)
            output_zip = workspace.file('output', archive_filename)
            progress(
                ExternalMediaProjectExport.Status.BUILDING_TIMELINE,
                18,
                'Construindo timeline a partir dos arquivos originais',
            )
            timeline, report, timeline_path, archive_path = PremierePackageService().build(
                export.project,
                package_root,
                output_zip,
                InternalTimelineBuilder(),
                progress=progress,
            )
            if export.archive:
                export.archive.delete(save=False)
            if export.timeline_json:
                export.timeline_json.delete(save=False)
            with archive_path.open('rb') as handle:
                export.archive.save(archive_filename, File(handle), save=False)
            with timeline_path.open('rb') as handle:
                export.timeline_json.save('timeline.json', File(handle), save=False)
        export.compatibility = timeline.get('compatibility') or {}
        export.validation_report = report
        export.timeline_revision = timeline.get('timeline_revision')
        export.status = ExternalMediaProjectExport.Status.FINISHED
        export.progress = 100
        export.current_step = 'Projeto editável pronto'
        export.finished_at = timezone.now()
        export.save(update_fields=[
            'archive', 'timeline_json', 'compatibility', 'validation_report', 'timeline_revision', 'status',
            'progress', 'current_step', 'finished_at', 'update_at',
        ])
    except Exception as exc:
        ExternalMediaProjectExport.objects.filter(pk=export_id).update(
            status=ExternalMediaProjectExport.Status.ERROR,
            current_step='Exportação interrompida',
            error_message=str(exc) or 'Não foi possível criar o pacote para o Premiere.',
            finished_at=timezone.now(),
        )
        raise
