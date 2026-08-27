from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from celery import shared_task
from django.conf import settings
from django.core.files import File
from django.utils import timezone

from ..models.external_media import ExternalMediaProjectExport, ProjectBlockMedia, VideoMasteringJob
from .audio_analysis import AudioAnalysisService
from .audio_mastering import AudioMasteringService, MasteringTarget
from .audio_muxing import AudioMuxingService
from .audio_validation import AudioValidationService
from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner
from .premiere_export import PremierePackageService
from .services import ExternalMediaPipeline, ExternalMediaProjectPipeline, VideoAssemblyService
from .timeline import InternalTimelineBuilder


@shared_task(bind=True, autoretry_for=(), name='external_media.prepare_subtitles')
def prepare_external_media(self, job_id):
    ExternalMediaPipeline().prepare_subtitles(job_id)


@shared_task(bind=True, autoretry_for=(), name='external_media.render_outputs')
def render_external_media(self, job_id):
    ExternalMediaPipeline().render_outputs(job_id)


@shared_task(bind=True, autoretry_for=(), name='external_media.run_project')
def run_external_media_project(self, project_id):
    ExternalMediaProjectPipeline().run(project_id)


@shared_task(bind=True, autoretry_for=(), name='external_media.create_project_preview')
def create_project_preview(self, media_id):
    item = ProjectBlockMedia.objects.get(pk=media_id)
    item.preview_status = ProjectBlockMedia.PreviewStatus.PENDING
    item.preview_error = ''
    item.save(update_fields=['preview_status', 'preview_error', 'update_at'])
    try:
        with TemporaryDirectory(prefix='connect-project-preview-') as temp:
            workdir = Path(temp)
            source = workdir / f'source{Path(item.file.name).suffix.lower()}'
            preview = workdir / 'preview.mp4'
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


@shared_task(bind=True, autoretry_for=(), name='external_media.render_project')
def render_external_media_project(self, project_id):
    ExternalMediaProjectPipeline().render(project_id)


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
        with TemporaryDirectory(prefix='connect-mastering-analysis-') as temp:
            workdir = Path(temp)
            source = workdir / f'original{Path(job.original_video.name).suffix.lower()}'
            audio = workdir / 'audio_extracted.wav'
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
        with TemporaryDirectory(prefix='connect-mastering-') as temp:
            workdir = Path(temp)
            source = workdir / f'original{Path(job.original_video.name).suffix.lower()}'
            extracted = workdir / 'audio_extracted.wav'
            mastered = workdir / 'audio_mastered.wav'
            output = workdir / 'video_mastered.mp4'
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
        with TemporaryDirectory(prefix='connect-premiere-export-') as temp:
            workdir = Path(temp)
            package_root = workdir / 'package'
            output_zip = workdir / 'premiere_project.zip'
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
                export.archive.save('premiere_project.zip', File(handle), save=False)
            with timeline_path.open('rb') as handle:
                export.timeline_json.save('timeline.json', File(handle), save=False)
        export.compatibility = timeline.get('compatibility') or {}
        export.validation_report = report
        export.status = ExternalMediaProjectExport.Status.FINISHED
        export.progress = 100
        export.current_step = 'Projeto editável pronto'
        export.finished_at = timezone.now()
        export.save(update_fields=[
            'archive', 'timeline_json', 'compatibility', 'validation_report', 'status',
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
