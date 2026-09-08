import mimetypes
import json
import re
import uuid
from copy import deepcopy
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.http import content_disposition_header
from django.views import View
from safedelete.models import HARD_DELETE

from ..external_media.tasks import (
    analyze_video_mastering,
    create_project_preview,
    export_premiere_project,
    master_video,
    prepare_external_media,
    render_external_media,
    render_external_media_project,
    run_external_media_project,
)
from ..forms.external_media import (
    ExternalMediaJobForm,
    ExternalMediaProjectEditForm,
    ExternalMediaProjectForm,
    ExternalMediaProjectSettingsForm,
    GlossaryTermForm,
    ProjectBlockMediaForm,
    ProjectCustomBlockForm,
    VideoMasteringProfileForm,
    VideoMasteringUploadForm,
)
from ..models.external_media import (
    ExternalMediaJob,
    ExternalMediaProject,
    ExternalMediaProjectExport,
    GlossaryTerm,
    MediaAsset,
    MediaTemplateBlock,
    ProjectPipelineStep,
    ProjectBlockMedia,
    ProjectCustomBlock,
    ProjectSourceProxy,
    ProjectOverlay,
    OverlayPreset,
    SubtitleCue,
    SubtitleReviewSession,
    SubtitleTrack,
    VideoMasteringJob,
)
from ..external_media.services import MusicService, ProjectService
from tempfile import TemporaryDirectory

from ..external_media.audio_noise import AudioCleanupService, NoiseReductionDecision, ReductionMode
from ..external_media.preview import PreviewCompositionService, TimelineRevisionService
from ..external_media.overlays import OverlayAssetRenderer, OverlayTimelineService
from ..external_media.services import StorageService
from ..external_media.subtitle_reviews import SubtitleReviewService
from .mixins import ExternalMediaRequiredMixin


_RANGE_RE = re.compile(r'bytes=(\d*)-(\d*)')


def project_processing_estimate(project):
    """Returns a conservative ETA based on this project's completed renders."""
    if not project.started_at or project.status in {
        ExternalMediaProject.Status.FINISHED,
        ExternalMediaProject.Status.ERROR,
        ExternalMediaProject.Status.CANCELLED,
        ExternalMediaProject.Status.DRAFT,
    }:
        return None, None, None
    elapsed_seconds = max(0, int((timezone.now() - project.started_at).total_seconds()))
    completed = list(
        project.processing_history.filter(
            status=ExternalMediaJob.Status.FINISHED,
            started_at__isnull=False,
            finished_at__isnull=False,
        ).exclude(pk=project.render_job_id).order_by('-finished_at')[:5]
    )
    durations = [max(1, int((job.finished_at - job.started_at).total_seconds())) for job in completed]
    historical_total = sorted(durations)[len(durations) // 2] if durations else None
    pace_total = None
    if project.progress >= 12 and elapsed_seconds >= 20:
        pace_total = round(elapsed_seconds * 100 / max(1, project.progress))
    if historical_total and pace_total:
        estimated_total, source = round(historical_total * 0.8 + pace_total * 0.2), 'histórico e andamento atual'
    elif historical_total:
        estimated_total, source = historical_total, 'histórico deste projeto'
    elif pace_total:
        estimated_total, source = pace_total, 'andamento atual'
    else:
        return elapsed_seconds, None, None
    return elapsed_seconds, max(0, estimated_total - elapsed_seconds), source


def _file_chunks(file_handle, start, length, block_size=8192):
    file_handle.seek(start)
    remaining = length
    while remaining > 0:
        chunk = file_handle.read(min(block_size, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        yield chunk


def protected_file_response(request, field_file, force_stream=False):
    """Serve locally (with HTTP Range for video seek) or redirect to a signed S3 URL."""
    filename = Path(field_file.name).name
    preview = request.GET.get('preview') == '1'
    content_type = mimetypes.guess_type(field_file.name)[0] or 'application/octet-stream'
    if settings.USE_S3 and not force_stream:
        disposition = 'inline' if preview else 'attachment'
        try:
            url = field_file.storage.url(
                field_file.name,
                parameters={'ResponseContentDisposition': f'{disposition}; filename="{filename}"'},
            )
        except TypeError:
            url = field_file.url
        return redirect(url)
    try:
        file_handle = field_file.open('rb')
    except FileNotFoundError as exc:
        raise Http404('Arquivo não encontrado.') from exc

    # FileResponse in Django 4.2 does not honor Range requests. HTML5 <video> seeking
    # requires Accept-Ranges + 206 Partial Content, otherwise the scrubber stays stuck.
    try:
        file_size = int(getattr(field_file, 'size', None) or 0)
    except (TypeError, ValueError, OSError):
        file_size = 0
    if not file_size and hasattr(file_handle, 'seek') and hasattr(file_handle, 'tell'):
        current = file_handle.tell()
        file_handle.seek(0, 2)
        file_size = file_handle.tell()
        file_handle.seek(current)

    disposition = content_disposition_header(not preview, filename)
    range_header = (request.META.get('HTTP_RANGE') or '').strip()
    if range_header and file_size > 0:
        match = _RANGE_RE.fullmatch(range_header)
        if not match or (not match.group(1) and not match.group(2)):
            file_handle.close()
            return HttpResponse(status=416, headers={'Content-Range': f'bytes */{file_size}'})
        start_raw, end_raw = match.groups()
        if start_raw == '':
            # bytes=-N → last N bytes
            length = min(int(end_raw), file_size)
            start = file_size - length
            end = file_size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else file_size - 1
            end = min(end, file_size - 1)
        if start < 0 or start > end or start >= file_size:
            file_handle.close()
            return HttpResponse(status=416, headers={'Content-Range': f'bytes */{file_size}'})
        length = end - start + 1
        response = StreamingHttpResponse(
            _file_chunks(file_handle, start, length),
            status=206,
            content_type=content_type,
        )
        response['Content-Length'] = str(length)
        response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        response['Accept-Ranges'] = 'bytes'
        if disposition:
            response['Content-Disposition'] = disposition
        response._resource_closers.append(file_handle.close)
        return response

    response = FileResponse(
        file_handle,
        content_type=content_type,
        as_attachment=not preview,
        filename=filename,
    )
    response['Accept-Ranges'] = 'bytes'
    return response


class ExternalMediaContextMixin:
    def media_context(self, **kwargs):
        return {
            'member': self.member,
            'is_external_media_member': True,
            'can_consolidate': self.member.is_available_to_consolidate,
            'is_ministration_member': False,
            'external_media_max_upload_mb': settings.EXTERNAL_MEDIA_MAX_UPLOAD_MB,
            **kwargs,
        }


class ExternalMediaDashboardView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    def get(self, request):
        query = request.GET.get('q', '').strip()
        status = request.GET.get('status', 'all')

        projects = ExternalMediaProject.objects.select_related(
            'created_by', 'template_version__template', 'render_job',
        ).order_by('-created_at')

        if query:
            projects = projects.filter(
                Q(name__icontains=query) | Q(template_version__template__name__icontains=query),
            )

        if status == 'processing':
            projects = projects.exclude(status__in=[
                ExternalMediaProject.Status.FINISHED,
                ExternalMediaProject.Status.ERROR,
                ExternalMediaProject.Status.CANCELLED,
            ])
        elif status == 'finished':
            projects = projects.filter(status=ExternalMediaProject.Status.FINISHED)
        elif status == 'error':
            projects = projects.filter(status=ExternalMediaProject.Status.ERROR)

        all_projects = ExternalMediaProject.objects.all()
        summary = ExternalMediaJob.objects.filter(processing_project__isnull=False).aggregate(
            total=Sum('assets__download_count'),
        )

        return render(request, 'member/external_media/dashboard.html', self.media_context(
            query=query,
            status=status,
            total=projects.count(),
            projects=projects[:50],
            total_all=all_projects.count(),
            processing_count=all_projects.exclude(status__in=[
                ExternalMediaProject.Status.FINISHED,
                ExternalMediaProject.Status.ERROR,
                ExternalMediaProject.Status.CANCELLED,
            ]).count(),
            finished_count=all_projects.filter(status=ExternalMediaProject.Status.FINISHED).count(),
            total_downloads=summary['total'] or 0,
            mastering_jobs=VideoMasteringJob.objects.select_related(
                'created_by', 'mastering_profile',
            ).order_by('-created_at')[:50],
        ))


class VideoMasteringCreateView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    def get(self, request):
        return render(request, 'member/external_media/mastering_create.html', self.media_context(
            form=VideoMasteringUploadForm(),
        ))

    def post(self, request):
        form = VideoMasteringUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, 'member/external_media/mastering_create.html', self.media_context(form=form))
        job = form.save(commit=False)
        job.created_by = self.member
        job.status = VideoMasteringJob.Status.ANALYZING
        job.progress = 5
        job.current_step = 'Análise adicionada à fila'
        job.save()
        try:
            result = analyze_video_mastering.delay(job.pk)
            job.celery_task_id = result.id
            job.save(update_fields=['celery_task_id', 'update_at'])
        except Exception:
            job.status = VideoMasteringJob.Status.ERROR
            job.error_message = 'Não foi possível acessar a fila. Verifique o Redis e o worker.'
            job.save(update_fields=['status', 'error_message', 'update_at'])
        return redirect('external_media_mastering_detail', public_id=job.public_id)


class VideoMasteringDetailView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    def get(self, request, public_id):
        job = get_object_or_404(
            VideoMasteringJob.objects.select_related('created_by', 'mastering_profile'),
            public_id=public_id,
        )
        initial = {'mastering_profile': job.mastering_profile_id} if job.mastering_profile_id else None
        return render(request, 'member/external_media/mastering_detail.html', self.media_context(
            job=job,
            profile_form=VideoMasteringProfileForm(initial=initial),
        ))


class VideoMasteringRunView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        job = get_object_or_404(VideoMasteringJob, public_id=public_id)
        if job.status not in {
            VideoMasteringJob.Status.READY,
            VideoMasteringJob.Status.FINISHED,
            VideoMasteringJob.Status.ERROR,
        }:
            messages.warning(request, 'Aguarde a etapa atual terminar.')
            return redirect('external_media_mastering_detail', public_id=public_id)
        form = VideoMasteringProfileForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Selecione um perfil de masterização válido.')
            return redirect('external_media_mastering_detail', public_id=public_id)
        job.mastering_profile = form.cleaned_data['mastering_profile']
        job.status = VideoMasteringJob.Status.MASTERING
        job.progress = 30
        job.current_step = 'Masterização adicionada à fila'
        job.error_message = ''
        job.finished_at = None
        job.save(update_fields=[
            'mastering_profile', 'status', 'progress', 'current_step', 'error_message',
            'finished_at', 'update_at',
        ])
        try:
            result = master_video.delay(job.pk)
            job.celery_task_id = result.id
            job.save(update_fields=['celery_task_id', 'update_at'])
        except Exception:
            job.status = VideoMasteringJob.Status.ERROR
            job.error_message = 'Não foi possível acessar a fila. Verifique o Redis e o worker.'
            job.save(update_fields=['status', 'error_message', 'update_at'])
        return redirect('external_media_mastering_detail', public_id=public_id)


class VideoMasteringStatusView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id):
        job = get_object_or_404(VideoMasteringJob, public_id=public_id)
        return JsonResponse({
            'status': job.status,
            'status_label': job.get_status_display(),
            'progress': job.progress,
            'current_step': job.current_step,
            'duration_label': job.duration_label,
            'error': job.error_message,
            'is_terminal': job.status in {
                VideoMasteringJob.Status.READY,
                VideoMasteringJob.Status.FINISHED,
                VideoMasteringJob.Status.ERROR,
            },
            'detail_url': reverse('external_media_mastering_detail', kwargs={'public_id': public_id}),
        })


class VideoMasteringOriginalView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id):
        job = get_object_or_404(VideoMasteringJob, public_id=public_id)
        return protected_file_response(request, job.original_video)


class VideoMasteringOutputView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id):
        job = get_object_or_404(VideoMasteringJob, public_id=public_id)
        if not job.output_video:
            raise Http404('O vídeo masterizado ainda não está disponível.')
        if request.GET.get('preview') != '1':
            VideoMasteringJob.objects.filter(pk=job.pk).update(download_count=F('download_count') + 1)
        return protected_file_response(request, job.output_video)


class ExternalMediaCreateView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    def get(self, request):
        return render(request, 'member/external_media/create.html', self.media_context(
            form=ExternalMediaProjectForm(),
        ))

    def post(self, request):
        form = ExternalMediaProjectForm(request.POST)
        if not form.is_valid():
            return render(request, 'member/external_media/create.html', self.media_context(form=form))
        project = form.save(commit=False)
        project.created_by = self.member
        project.configuration = dict(project.template_version.default_settings)
        project.save()
        messages.success(request, 'Projeto criado. Agora envie os vídeos de cada bloco.')
        return redirect('external_media_project_detail', public_id=project.public_id)


class ExternalMediaProjectDetailView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    @staticmethod
    def _can_retry_project_render(project):
        job = project.render_job
        if not job:
            return False
        available_languages = set(job.subtitle_tracks.values_list('language', flat=True))
        return set(job.output_languages).issubset(available_languages)

    def get_project(self, public_id):
        return get_object_or_404(
            ExternalMediaProject.objects.select_related(
                'created_by', 'template_version__template', 'template_version__preset',
                'template_version__subtitle_style', 'render_job',
            ).prefetch_related(
                'template_version__blocks', 'template_version__plugins',
                'block_media', 'custom_blocks', 'pipeline_steps', 'render_job__assets',
                'processing_history__assets', 'subtitle_review_sessions',
            ),
            public_id=public_id,
        )

    def get(self, request, public_id):
        project = self.get_project(public_id)
        project_overlays = OverlayTimelineService.ensure_project_overlays(project)
        overlays_by_block = {}
        for overlay in project_overlays:
            overlay.form_fields = [
                {
                    'key': key,
                    'label': options.get('label') or key.replace('_', ' ').title(),
                    'required': bool(options.get('required')),
                    'placeholder': options.get('placeholder', ''),
                    'max_length': int(options.get('max_length') or 2048),
                    'value': (overlay.content or {}).get(
                        key, options.get('default', options.get('default_value', '')),
                    ),
                }
                for key, options in (overlay.content_schema or {}).items()
                if isinstance(options, dict)
            ]
            overlays_by_block.setdefault(overlay.block_id, []).append(overlay)
        restore_job = None
        restore_job_id = (project.configuration or {}).get('_editable_previous_render_job_id')
        if project.status == ExternalMediaProject.Status.DRAFT and restore_job_id:
            restore_job = next(
                (job for job in project.processing_history.all()
                 if job.pk == restore_job_id and job.status == ExternalMediaJob.Status.FINISHED),
                None,
            )
        media_by_block = {}
        missing_media_by_block = {}
        for item in project.block_media.all():
            if ProjectService.file_exists(item.file):
                media_by_block.setdefault(('custom', item.custom_block_id) if item.custom_block_id else ('template', item.block_id), []).append(item)
            else:
                missing_media_by_block.setdefault(('custom', item.custom_block_id) if item.custom_block_id else ('template', item.block_id), []).append(item)
        blocks = [
            {
                'definition': block,
                'media': media_by_block.get(('template', block.pk), []),
                'missing_media': missing_media_by_block.get(('template', block.pk), []),
                'has_default_video': ProjectService.file_exists(block.default_video),
                'has_missing_default_video': bool(block.default_video and block.default_video.name),
                'is_custom': False,
                'overlays': overlays_by_block.get(block.pk, []),
            }
            for block in project.template_version.blocks.all()
        ]
        blocks.extend({
            'definition': block,
            'media': media_by_block.get(('custom', block.pk), []),
            'missing_media': missing_media_by_block.get(('custom', block.pk), []),
            'has_default_video': False,
            'has_missing_default_video': False,
            'is_custom': True,
            'overlays': [],
        } for block in project.custom_blocks.all())
        block_by_key = {
            f"{'c' if item['is_custom'] else 't'}-{item['definition'].pk}": item
            for item in blocks
        }
        ordered_blocks = []
        for key in (project.configuration or {}).get('block_order', []):
            item = block_by_key.pop(key, None)
            if item:
                ordered_blocks.append(item)
        blocks = ordered_blocks + list(block_by_key.values())
        return render(request, 'member/external_media/project_detail.html', self.media_context(
            project=project,
            blocks=blocks,
            plugins=project.template_version.plugins.all(),
            steps=project.pipeline_steps.all(),
            upload_form=ProjectBlockMediaForm(),
            custom_block_form=ProjectCustomBlockForm(),
            settings_form=ExternalMediaProjectSettingsForm(project=project),
            assets=project.render_job.assets.all() if project.render_job_id else [],
            validation_errors=ProjectService.validate_uploads(project),
            can_retry_render=self._can_retry_project_render(project),
            restore_processed_result=restore_job,
            project_exports=project.exports.order_by('-created_at')[:10],
            processing_history=project.processing_history.select_related('preset').prefetch_related('assets'),
            pending_subtitle_reviews=project.subtitle_review_sessions.filter(
                status__in=[
                    SubtitleReviewSession.Status.SUBMITTED,
                    SubtitleReviewSession.Status.UNDER_REVIEW,
                ],
            ).count(),
        ))


class ExternalMediaProjectEditView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    def get_project(self, public_id):
        return get_object_or_404(
            ExternalMediaProject.objects.select_related('template_version__template'),
            public_id=public_id,
        )

    def get(self, request, public_id):
        project = self.get_project(public_id)
        return render(request, 'member/external_media/project_edit.html', self.media_context(
            project=project,
            form=ExternalMediaProjectEditForm(instance=project),
        ))

    def post(self, request, public_id):
        project = self.get_project(public_id)
        form = ExternalMediaProjectEditForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request, 'Nome do projeto atualizado.')
            return redirect('external_media_project_detail', public_id=project.public_id)
        return render(request, 'member/external_media/project_edit.html', self.media_context(
            project=project, form=form,
        ))


class ExternalMediaProjectResumeEditingView(ExternalMediaRequiredMixin, View):
    """Returns a terminal project to the upload screen without losing its media/history."""

    editable_statuses = {
        ExternalMediaProject.Status.FINISHED,
        ExternalMediaProject.Status.ERROR,
        ExternalMediaProject.Status.CANCELLED,
    }

    def post(self, request, public_id):
        with transaction.atomic():
            project = get_object_or_404(
                ExternalMediaProject.objects.select_for_update().prefetch_related('template_version__plugins'),
                public_id=public_id,
            )
            if project.status not in self.editable_statuses:
                messages.warning(request, 'Aguarde o processamento terminar antes de editar os vídeos.')
                return redirect('external_media_project_detail', public_id=project.public_id)

            # The current job stays in processing_history, including generated files,
            # while the project itself becomes editable and its next run gets a new job.
            configuration = dict(project.configuration or {})
            configuration['_editable_previous_render_job_id'] = project.render_job_id
            configuration['_editable_previous_steps'] = list(project.pipeline_steps.values(
                'code', 'status', 'progress', 'message',
            ))
            project.status = ExternalMediaProject.Status.DRAFT
            project.progress = 0
            project.current_step = ''
            project.error_message = ''
            project.started_at = None
            project.finished_at = None
            project.celery_task_id = ''
            project.render_job = None
            project.configuration = configuration
            project.save(update_fields=[
                'status', 'progress', 'current_step', 'error_message', 'started_at',
                'finished_at', 'celery_task_id', 'render_job', 'configuration', 'update_at',
            ])
            ProjectService().initialize_steps(project, project.template_version.plugins.all())
        messages.success(request, 'Projeto aberto para edição. Seus vídeos e cortes foram preservados.')
        return redirect('external_media_project_detail', public_id=project.public_id)


class ExternalMediaProjectRestoreProcessedResultView(ExternalMediaRequiredMixin, View):
    """Reattaches the latest finished result when the user leaves edit mode."""

    def post(self, request, public_id):
        with transaction.atomic():
            project = get_object_or_404(
                ExternalMediaProject.objects.select_for_update().prefetch_related('pipeline_steps'),
                public_id=public_id, status=ExternalMediaProject.Status.DRAFT,
            )
            configuration = dict(project.configuration or {})
            job_id = configuration.get('_editable_previous_render_job_id')
            job = get_object_or_404(
                ExternalMediaJob, pk=job_id, processing_project=project,
                status=ExternalMediaJob.Status.FINISHED,
            )
            previous_steps = configuration.pop('_editable_previous_steps', [])
            configuration.pop('_editable_previous_render_job_id', None)
            project.status = ExternalMediaProject.Status.FINISHED
            project.progress = 100
            project.current_step = 'Processamento finalizado'
            project.error_message = ''
            project.started_at = job.started_at
            project.finished_at = job.finished_at
            project.render_job = job
            project.configuration = configuration
            project.save(update_fields=[
                'status', 'progress', 'current_step', 'error_message', 'started_at',
                'finished_at', 'render_job', 'configuration', 'update_at',
            ])
            for step in previous_steps:
                ProjectPipelineStep.objects.filter(project=project, code=step['code']).update(
                    status=step['status'], progress=step['progress'], message=step['message'],
                )
        messages.success(request, 'Resultado processado restaurado. Nenhuma nova renderização foi iniciada.')
        return redirect('external_media_project_detail', public_id=project.public_id)


class ExternalMediaProjectDeleteView(ExternalMediaRequiredMixin, View):
    terminal_statuses = {
        ExternalMediaProject.Status.DRAFT,
        ExternalMediaProject.Status.FINISHED,
        ExternalMediaProject.Status.ERROR,
        ExternalMediaProject.Status.CANCELLED,
    }

    def post(self, request, public_id):
        project = get_object_or_404(
            ExternalMediaProject.objects.select_related('render_job').prefetch_related(
                'block_media', 'exports', 'render_job__assets', 'processing_history__assets',
            ),
            public_id=public_id,
        )
        if project.status not in self.terminal_statuses:
            messages.warning(request, 'Aguarde o processamento terminar antes de excluir o projeto.')
            return redirect('external_media_dashboard')

        files = []

        def remember(field_file):
            if field_file and field_file.name:
                files.append((field_file.storage, field_file.name))

        for item in project.block_media.all():
            remember(item.file)
            remember(item.thumbnail)
        for export in project.exports.all():
            remember(export.archive)
            remember(export.timeline_json)
        processing_jobs = list(project.processing_history.all())
        if project.render_job_id and all(job.pk != project.render_job_id for job in processing_jobs):
            # Compatibilidade para execuções feitas antes do histórico de projeto.
            processing_jobs.append(project.render_job)
        for job in processing_jobs:
            remember(job.original_video)
            for asset in job.assets.all():
                remember(asset.file)

        project_name = project.name
        with transaction.atomic():
            # A remoção é feita antes do projeto porque o job atual é referenciado por
            # `render_job`; o banco então limpa esse ponteiro sem deixar histórico órfão.
            for job in processing_jobs:
                job.delete(force_policy=HARD_DELETE)
            project.delete(force_policy=HARD_DELETE)

            def remove_files():
                for storage, name in files:
                    try:
                        storage.delete(name)
                    except Exception:
                        # A exclusão do registro já foi concluída; uma mídia ausente
                        # não deve impedir a limpeza do projeto.
                        continue

            transaction.on_commit(remove_files)
        messages.success(request, f'Projeto “{project_name}” excluído.')
        return redirect('external_media_dashboard')


class ExternalMediaProjectUploadView(ExternalMediaRequiredMixin, View):
    @staticmethod
    def _error_response(request, public_id, detail):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'detail': detail}, status=400)
        messages.error(request, detail)
        return redirect('external_media_project_detail', public_id=public_id)

    def post(self, request, public_id, block_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        if project.status != ExternalMediaProject.Status.DRAFT:
            return self._error_response(request, public_id, 'Os uploads ficam bloqueados após iniciar o pipeline.')
        block = get_object_or_404(MediaTemplateBlock, pk=block_id, version=project.template_version)
        form = ProjectBlockMediaForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._error_response(request, public_id, 'Não foi possível enviar o vídeo: ' + ' '.join(
                error for errors in form.errors.values() for error in errors
            ))
        existing_camera_key = (form.cleaned_data.get('camera_key') or '').strip()
        existing_camera = project.block_media.filter(
            block=block, camera_key=existing_camera_key,
        ).order_by('camera_order', 'pk').first() if existing_camera_key else None
        requested_camera_role = (
            existing_camera.camera_role if existing_camera
            else form.cleaned_data.get('camera_role') or ProjectBlockMedia.CameraRole.PRIMARY
        )
        is_extra_camera = requested_camera_role == ProjectBlockMedia.CameraRole.SECONDARY
        count = sum(
            1 for existing in project.block_media.filter(
                block=block, camera_role=ProjectBlockMedia.CameraRole.PRIMARY,
            ) if ProjectService.file_exists(existing.file)
        )
        if not existing_camera and not is_extra_camera and block.max_occurrences > 0 and count >= block.max_occurrences:
            return self._error_response(request, public_id, f'O bloco {block.name} aceita no máximo {block.max_occurrences} vídeo(s).')
        item = form.save(commit=False)
        item.project = project
        item.block = block
        item.camera_role = requested_camera_role
        item.camera_hint = form.cleaned_data.get('camera_hint') or ProjectBlockMedia.CameraHint.AUTO
        if existing_camera:
            item.position = (
                ProjectBlockMedia.all_objects.filter(project=project, block=block)
                .aggregate(value=Max('position'))['value'] or 0
            ) + 1
            item.camera_order = 1
            item.camera_key = existing_camera.camera_key
            item.camera_label = existing_camera.camera_label
            item.camera_hint = existing_camera.camera_hint
        elif is_extra_camera:
            try:
                item.position = int(request.POST.get('camera_position') or 0)
            except (TypeError, ValueError):
                item.position = 0
            primary_exists = project.block_media.filter(
                block=block, position=item.position,
                camera_role=ProjectBlockMedia.CameraRole.PRIMARY,
            ).exists()
            if not primary_exists:
                return self._error_response(request, public_id, 'Escolha o vídeo principal ao qual esta câmera pertence.')
            item.camera_order = (
                ProjectBlockMedia.all_objects.filter(project=project, block=block, position=item.position)
                .aggregate(value=Max('camera_order'))['value'] or 0
            ) + 1
            item.camera_key = f'camera-{get_random_string(12).lower()}'
        else:
            item.position = (
                ProjectBlockMedia.all_objects.filter(project=project, block=block)
                .aggregate(value=Max('position'))['value'] or 0
            ) + 1
            item.camera_order = 1
            item.camera_role = ProjectBlockMedia.CameraRole.PRIMARY
            item.camera_key = 'primary'
        item.camera_label = item.camera_label.strip() or (
            'Câmera extra' if is_extra_camera else 'Câmera principal'
        )
        item.original_filename = item.file.name
        item.file_size = item.file.size
        item.trim_start_ms = ProjectBlockMediaForm.seconds_to_ms(form.cleaned_data.get('trim_start_seconds')) or 0
        item.trim_end_ms = ProjectBlockMediaForm.seconds_to_ms(form.cleaned_data.get('trim_end_seconds'))
        item.preview_status = ProjectBlockMedia.PreviewStatus.PENDING
        item.save()
        create_project_preview.delay(item.pk)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'id': item.pk,
                'name': item.original_filename,
                'size': item.file_size,
                'status_url': reverse('external_media_project_media_status', args=[project.public_id, item.pk]),
                'delete_url': reverse('external_media_project_media_delete', args=[project.public_id, item.pk]),
            })
        messages.success(request, f'Vídeo adicionado ao bloco {block.name}.')
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectSettingsView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        project = get_object_or_404(
            ExternalMediaProject.objects.prefetch_related('template_version__plugins'),
            public_id=public_id,
            status=ExternalMediaProject.Status.DRAFT,
        )
        form = ExternalMediaProjectSettingsForm(request.POST, project=project)
        if form.is_valid():
            form.save()
            messages.success(request, 'Configurações do projeto atualizadas.')
        else:
            messages.error(request, 'Não foi possível atualizar as configurações.')
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectCustomBlockView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, block_id=None):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id, status=ExternalMediaProject.Status.DRAFT)
        instance = get_object_or_404(ProjectCustomBlock, pk=block_id, project=project) if block_id else None
        form = ProjectCustomBlockForm(request.POST, instance=instance)
        if form.is_valid():
            custom_block = form.save(commit=False)
            custom_block.project = project
            if not custom_block.pk:
                custom_block.position = (project.custom_blocks.aggregate(value=Max('position'))['value'] or 0) + 1
            custom_block.save()
            messages.success(request, 'Bloco personalizado salvo.')
        else:
            messages.error(request, 'Informe um nome para o bloco personalizado.')
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectCustomBlockDeleteView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, block_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id, status=ExternalMediaProject.Status.DRAFT)
        get_object_or_404(ProjectCustomBlock, pk=block_id, project=project).delete()
        messages.success(request, 'Bloco personalizado removido.')
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectCustomBlockReorderView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id, status=ExternalMediaProject.Status.DRAFT)
        item_ids = request.POST.getlist('item_ids[]')
        template_ids = {f't-{pk}' for pk in project.template_version.blocks.values_list('pk', flat=True)}
        custom_ids = {f'c-{pk}' for pk in project.custom_blocks.values_list('pk', flat=True)}
        expected = template_ids | custom_ids
        if set(item_ids) != expected or len(item_ids) != len(expected):
            return JsonResponse({'detail': 'Ordem de blocos inválida.'}, status=400)
        configuration = dict(project.configuration or {})
        configuration['block_order'] = item_ids
        project.configuration = configuration
        project.save(update_fields=['configuration', 'update_at'])
        return JsonResponse({'ok': True})


class ExternalMediaProjectCustomBlockUploadView(ExternalMediaProjectUploadView):
    def post(self, request, public_id, block_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id, status=ExternalMediaProject.Status.DRAFT)
        block = get_object_or_404(ProjectCustomBlock, pk=block_id, project=project)
        form = ProjectBlockMediaForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._error_response(request, public_id, 'Não foi possível enviar o vídeo: ' + ' '.join(error for errors in form.errors.values() for error in errors))
        item = form.save(commit=False)
        item.project = project
        item.custom_block = block
        item.position = (ProjectBlockMedia.objects.filter(project=project, custom_block=block).aggregate(value=Max('position'))['value'] or 0) + 1
        item.original_filename = item.file.name
        item.file_size = item.file.size
        item.preview_status = ProjectBlockMedia.PreviewStatus.PENDING
        item.save()
        create_project_preview.delay(item.pk)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'id': item.pk, 'name': item.original_filename, 'size': item.file_size, 'status_url': reverse('external_media_project_media_status', args=[project.public_id, item.pk]), 'delete_url': reverse('external_media_project_media_delete', args=[project.public_id, item.pk])})
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectMediaDeleteView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, media_id):
        item = get_object_or_404(
            ProjectBlockMedia, pk=media_id, project__public_id=public_id,
            project__status=ExternalMediaProject.Status.DRAFT,
        )
        item.file.delete(save=False)
        item.delete()
        messages.success(request, 'Vídeo removido do bloco.')
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectMediaPreviewView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, media_id):
        item = get_object_or_404(ProjectBlockMedia, pk=media_id, project__public_id=public_id)
        if not ProjectService.file_exists(item.file):
            raise Http404('O vídeo não está mais disponível.')
        request.GET = request.GET.copy()
        request.GET['preview'] = '1'
        preview = item.preview_file if item.preview_status == ProjectBlockMedia.PreviewStatus.READY else item.file
        return protected_file_response(request, preview)


class ExternalMediaProjectMediaStatusView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, media_id):
        item = get_object_or_404(ProjectBlockMedia, pk=media_id, project__public_id=public_id)
        return JsonResponse({
            'status': item.preview_status,
            'error': item.preview_error,
            'name': item.original_filename,
            'preview_url': reverse('external_media_project_media_preview', args=[public_id, item.pk]),
            'trim_url': reverse('external_media_project_media_trim', args=[public_id, item.pk]),
            'trim_start_ms': item.trim_start_ms,
            'trim_end_ms': item.trim_end_ms,
            'trim_ranges': item.trim_ranges or [],
        })


class ExternalMediaProjectMediaTrimView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, media_id):
        item = get_object_or_404(
            ProjectBlockMedia,
            pk=media_id,
            project__public_id=public_id,
            project__status=ExternalMediaProject.Status.DRAFT,
        )
        try:
            start = float(request.POST.get('trim_start_seconds') or 0)
            end_value = request.POST.get('trim_end_seconds')
            end = float(end_value) if end_value else None
        except (TypeError, ValueError):
            return JsonResponse({'detail': 'Informe tempos de corte válidos.'}, status=400)
        raw_ranges = request.POST.get('trim_ranges')
        ranges = []
        if raw_ranges:
            try:
                values = json.loads(raw_ranges)
            except (TypeError, ValueError, json.JSONDecodeError):
                return JsonResponse({'detail': 'Os trechos selecionados não são válidos.'}, status=400)
            if not isinstance(values, list) or len(values) > 20:
                return JsonResponse({'detail': 'Informe entre um e 20 trechos válidos.'}, status=400)
            previous_end = -1
            for value in values:
                if not isinstance(value, dict):
                    return JsonResponse({'detail': 'Os trechos selecionados não são válidos.'}, status=400)
                range_start, range_end = float(value.get('start_seconds', -1)), float(value.get('end_seconds', -1))
                if range_start < 0 or range_end <= range_start or range_start < previous_end:
                    return JsonResponse({'detail': 'Os trechos precisam estar em ordem e não podem se sobrepor.'}, status=400)
                ranges.append({'start_ms': ProjectBlockMediaForm.seconds_to_ms(range_start), 'end_ms': ProjectBlockMediaForm.seconds_to_ms(range_end)})
                previous_end = range_end
        if start < 0 or (end is not None and (end < 0 or end <= start)):
            return JsonResponse({'detail': 'O fim do corte precisa ser maior que o início.'}, status=400)

        item.trim_ranges = ranges
        item.trim_start_ms = ranges[0]['start_ms'] if ranges else (ProjectBlockMediaForm.seconds_to_ms(start) or 0)
        item.trim_end_ms = ranges[0]['end_ms'] if ranges else ProjectBlockMediaForm.seconds_to_ms(end)
        item.save(update_fields=['trim_start_ms', 'trim_end_ms', 'trim_ranges', 'update_at'])
        return JsonResponse({'ok': True})


class ExternalMediaProjectMediaReorderView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, block_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        if project.status != ExternalMediaProject.Status.DRAFT:
            return JsonResponse({'detail': 'Os vídeos só podem ser ordenados durante a edição.'}, status=409)
        block = get_object_or_404(MediaTemplateBlock, pk=block_id, version=project.template_version)
        raw_ids = request.POST.getlist('media_ids[]') or request.POST.getlist('media_ids')
        try:
            media_ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError):
            return JsonResponse({'detail': 'Ordem de vídeos inválida.'}, status=400)
        with transaction.atomic():
            items = list(
                ProjectBlockMedia.objects.select_for_update().filter(
                    project=project, block=block, pk__in=media_ids,
                )
            )
            if len(media_ids) != len(items) or len(set(media_ids)) != len(media_ids):
                return JsonResponse({'detail': 'A ordem precisa conter todos os vídeos do bloco uma única vez.'}, status=400)

            # A restrição (projeto, bloco, posição) é imediata no PostgreSQL.
            # Primeiro liberamos as posições atuais com valores temporários para
            # permitir trocas como 1 <-> 2 sem uma colisão de unicidade.
            for offset, item in enumerate(items, start=1):
                item.position = 30000 + offset
            ProjectBlockMedia.objects.bulk_update(items, ['position'])

            by_id = {item.pk: item for item in items}
            for position, media_id in enumerate(media_ids, start=1):
                by_id[media_id].position = position
            ProjectBlockMedia.objects.bulk_update(items, ['position'])
        return JsonResponse({'ok': True})


class ExternalMediaProjectRunView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        with transaction.atomic():
            project = get_object_or_404(
                ExternalMediaProject.objects.select_for_update().prefetch_related(
                    'template_version__blocks', 'block_media',
                ),
                public_id=public_id,
            )
            if project.status not in {
                ExternalMediaProject.Status.DRAFT,
                ExternalMediaProject.Status.ERROR,
                ExternalMediaProject.Status.FINISHED,
                ExternalMediaProject.Status.CANCELLED,
            }:
                messages.warning(request, 'Este projeto já foi iniciado.')
                return redirect('external_media_project_detail', public_id=public_id)
            errors = ProjectService.validate_uploads(project)
            if errors:
                for error in errors:
                    messages.error(request, error)
                return redirect('external_media_project_detail', public_id=public_id)
            project.status = ExternalMediaProject.Status.PENDING
            project.progress = 2
            project.current_step = 'Projeto adicionado à fila'
            project.error_message = ''
            project.current_timeline_revision = None
            project.approved_timeline_revision = None
            project.preview_dirty = False
            project.final_render_outdated = False
            project.save(update_fields=[
                'status', 'progress', 'current_step', 'error_message',
                'current_timeline_revision', 'approved_timeline_revision',
                'preview_dirty', 'final_render_outdated', 'update_at',
            ])
            project.preview_sessions.update(current_revision=None, undo_stack=[], redo_stack=[])
            transaction.on_commit(lambda: self._enqueue(project.pk))
        messages.success(request, 'Pipeline iniciado em segundo plano.')
        return redirect('external_media_project_detail', public_id=public_id)

    @staticmethod
    def _enqueue(project_id):
        task_id = str(uuid.uuid4())
        try:
            ExternalMediaProject.objects.filter(pk=project_id).update(celery_task_id=task_id)
            run_external_media_project.apply_async(args=[project_id], task_id=task_id)
        except Exception:
            ExternalMediaProject.objects.filter(pk=project_id).update(
                status=ExternalMediaProject.Status.ERROR,
                current_step='Não foi possível acessar a fila de processamento',
                error_message='Verifique se o Redis e o worker Celery estão ativos.',
            )


class ExternalMediaProjectRenderView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        if project.status not in {ExternalMediaProject.Status.ERROR, ExternalMediaProject.Status.AWAITING_REVIEW}:
            messages.warning(request, 'O projeto ainda não está pronto para renderização.')
            return redirect('external_media_project_detail', public_id=public_id)
        if project.template_version.interactive_preview_enabled and not project.approved_timeline_revision_id:
            messages.warning(request, 'Revise e aprove a edição antes de renderizar.')
            return redirect('external_media_project_preview', public_id=public_id)
        project.status = ExternalMediaProject.Status.PENDING
        project.progress = 84
        project.current_step = 'Renderização adicionada à fila'
        project.error_message = ''
        project.save(update_fields=['status', 'progress', 'current_step', 'error_message', 'update_at'])
        try:
            task_id = str(uuid.uuid4())
            project.celery_task_id = task_id
            project.save(update_fields=['celery_task_id', 'update_at'])
            render_external_media_project.apply_async(args=[project.pk], task_id=task_id)
        except Exception:
            project.status = ExternalMediaProject.Status.ERROR
            project.error_message = 'Verifique se o Redis e o worker Celery estão ativos.'
            project.save(update_fields=['status', 'error_message', 'update_at'])
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectPreviewView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    def get(self, request, public_id):
        project = get_object_or_404(
            ExternalMediaProject.objects.select_related(
                'template_version__template', 'template_version__preset', 'current_timeline_revision',
                'approved_timeline_revision', 'render_job',
            ).prefetch_related('template_version__plugins', 'source_proxies'),
            public_id=public_id,
        )
        if project.status not in {ExternalMediaProject.Status.AWAITING_REVIEW, ExternalMediaProject.Status.FINISHED}:
            messages.warning(request, 'O preview ficará disponível após a análise inicial.')
            return redirect('external_media_project_detail', public_id=public_id)
        revision = TimelineRevisionService.ensure_initial(project, self.member)
        session = TimelineRevisionService.session(project, self.member, revision)
        # Revisions are immutable snapshots. Older ones were created before the
        # normalized master was exposed to the browser, so enrich only this
        # response rather than rewriting editorial history just to change a URL.
        timeline = deepcopy(revision.timeline)
        # Caption styling was added after some revision snapshots already existed.
        # Enrich those read-only snapshots with the exact styles frozen on the job.
        if project.render_job_id:
            timeline['caption_styles'] = {
                'source': PreviewCompositionService.subtitle_style_payload(project.render_job.subtitle_style),
                'translated': PreviewCompositionService.subtitle_style_payload(
                    project.render_job.translated_subtitle_style or project.render_job.subtitle_style,
                ),
                'source_language': project.template_version.original_language,
            }
            for cue in timeline.get('captions', []):
                cue['is_source'] = cue.get('language') == project.template_version.original_language
        job = project.render_job
        if job and job.original_video and ProjectService.file_exists(job.original_video):
            timeline['review_master_url'] = reverse(
                'external_media_project_preview_master', kwargs={'public_id': project.public_id},
            )
            has_manual_transform = any(
                item.get('type') == 'reframe'
                and item.get('enabled', True)
                and (item.get('metadata') or {}).get('manual_transform')
                for item in revision.edit_decision_set.get('operations', [])
            )
            if not has_manual_transform and 'TRANSFORMS' in (timeline.get('fidelity') or {}):
                timeline['fidelity']['TRANSFORMS'] = 'EXACT'
        music = MusicService.selected_file(project.template_version)
        timeline['has_music'] = bool(music and ProjectService.file_exists(music))
        if music and ProjectService.file_exists(music):
            timeline['music'] = {
                'url': reverse('external_media_project_preview_music', kwargs={'public_id': project.public_id}),
                'volume': float(project.template_version.music_volume),
                'fade_in_seconds': float(project.template_version.fade_in_seconds),
                'fade_out_seconds': float(project.template_version.fade_out_seconds),
                'ducking_enabled': bool(
                    project.template_version.audio_mixing_enabled
                    and project.template_version.audio_ducking_enabled
                ),
                'duck_db': float((project.template_version.audio_mixing_config or {}).get('base_duck_db', 14)),
                'attack_ms': int((project.template_version.audio_mixing_config or {}).get('attack_ms', 140)),
                'hold_ms': int((project.template_version.audio_mixing_config or {}).get('hold_ms', 300)),
                'release_ms': int((project.template_version.audio_mixing_config or {}).get('release_ms', 850)),
                'speech_gap_hold_ms': int((project.template_version.audio_mixing_config or {}).get('speech_gap_hold_ms', 1800)),
            }
        return render(request, 'member/external_media/preview.html', self.media_context(
            project=project, revision=revision, session=session, timeline=timeline,
            overlay_presets=list(OverlayPreset.objects.filter(is_active=True).values(
                'code', 'name', 'overlay_type', 'style', 'position', 'animation',
            )),
        ))


class ExternalMediaProjectPreviewSourceView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, source_id):
        proxy = get_object_or_404(
            ProjectSourceProxy,
            project__public_id=public_id,
            source_id=source_id,
            status=ProjectSourceProxy.Status.READY,
        )
        request.GET = request.GET.copy()
        request.GET['preview'] = '1'
        return protected_file_response(request, proxy.proxy_file)


class ExternalMediaProjectPreviewMasterView(ExternalMediaRequiredMixin, View):
    """Serve the normalized review master, including the pipeline's auto reframe."""

    def get(self, request, public_id):
        project = get_object_or_404(
            ExternalMediaProject.objects.select_related('render_job'), public_id=public_id,
        )
        job = project.render_job
        if not job or not job.original_video or not ProjectService.file_exists(job.original_video):
            raise Http404('A prévia com enquadramento final ainda não está disponível.')
        request.GET = request.GET.copy()
        request.GET['preview'] = '1'
        return protected_file_response(request, job.original_video)


class ExternalMediaProjectPreviewMusicView(ExternalMediaRequiredMixin, View):
    """Streams the template music so it can be synchronized in the browser."""

    def get(self, request, public_id):
        project = get_object_or_404(
            ExternalMediaProject.objects.select_related(
                'template_version__background_music',
            ).prefetch_related('template_version__plugins'),
            public_id=public_id,
        )
        music = MusicService.selected_file(project.template_version)
        if not music or not ProjectService.file_exists(music):
            raise Http404('A trilha deste template não está disponível.')
        request.GET = request.GET.copy()
        request.GET['preview'] = '1'
        return protected_file_response(request, music)


class ExternalMediaProjectPreviewDecisionView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, decision_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        payload = json.loads(request.body or '{}')
        try:
            revision = TimelineRevisionService.mutate_decision(
                project, self.member, decision_id, payload.get('enabled', False),
            )
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=404)
        return JsonResponse({'revision': revision.revision, 'timeline': revision.timeline})


class ExternalMediaProjectPreviewCutView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        payload = json.loads(request.body or '{}')
        try:
            revision = TimelineRevisionService.create_manual_cut(
                project, self.member, payload.get('start_ms'), payload.get('end_ms'),
            )
        except (TypeError, ValueError) as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        return JsonResponse({'revision': revision.revision, 'timeline': revision.timeline})


class ExternalMediaProjectPreviewNoiseClipView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, decision_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        if not project.render_job_id:
            raise Http404
        revision = project.current_timeline_revision
        if not revision:
            raise Http404
        operation = next(
            (
                item for item in (revision.edit_decision_set.get('operations') or [])
                if item.get('id') == decision_id
            ),
            None,
        )
        if not operation or operation.get('type') != 'audio_noise_reduction':
            raise Http404
        metadata = operation.get('metadata') or {}
        variant = request.GET.get('variant', 'original')
        if variant not in {'original', 'treated'}:
            variant = 'original'
        decision = NoiseReductionDecision(
            int(operation.get('source_in_ms') or 0),
            int(operation.get('source_out_ms') or operation.get('source_in_ms') or 0),
            metadata.get('mode', ReductionMode.LOCAL),
            metadata.get('strength', 'LIGHT'),
            metadata.get('source', 'AUTO_NOISE_ANALYSIS'),
            metadata.get('noise_type', 'UNKNOWN_NOISE'),
            bool(operation.get('enabled', False)),
            metadata.get('recommended_action', 'REVIEW'),
            bool(metadata.get('speech_overlap')),
            float(operation.get('confidence') or 0),
            metadata.get('label') or operation.get('reason') or '',
            metadata.get('event_index'),
        )
        storage = StorageService()
        with TemporaryDirectory(prefix='connect-noise-preview-') as temp:
            workdir = Path(temp)
            source = workdir / f'source{Path(project.render_job.original_video.name).suffix.lower()}'
            output = workdir / f'{variant}.m4a'
            storage.copy_to_local(project.render_job.original_video, source)
            AudioCleanupService().render_preview_clip(source, output, decision, variant=variant)
            return FileResponse(
                output.open('rb'),
                content_type='audio/mp4',
                as_attachment=False,
                filename=f'noise-{decision_id}-{variant}.m4a',
            )


class ExternalMediaProjectPreviewSubtitleView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, cue_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        payload = json.loads(request.body or '{}')
        text = str(payload.get('text') or '').strip()
        if not text:
            return JsonResponse({'error': 'A legenda não pode ficar vazia.'}, status=400)
        revision = TimelineRevisionService.update_subtitle(project, self.member, cue_id, text)
        return JsonResponse({'revision': revision.revision, 'timeline': revision.timeline})


class ExternalMediaProjectPreviewTransformView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, decision_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        payload = json.loads(request.body or '{}')
        try:
            revision = TimelineRevisionService.update_transform(
                project, self.member, decision_id, payload,
            )
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=404)
        return JsonResponse({'revision': revision.revision, 'timeline': revision.timeline})


class ExternalMediaProjectOverlayDataView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, overlay_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        overlay = get_object_or_404(ProjectOverlay, project=project, overlay_id=overlay_id)
        if project.status != ExternalMediaProject.Status.DRAFT:
            messages.warning(request, 'Edite overlays processados diretamente no Preview.')
            return redirect('external_media_project_detail', public_id=public_id)
        content = {
            key: request.POST.get(f'content_{key}', '')
            for key in (overlay.content_schema or {})
        }
        image_upload = request.FILES.get('image_file')
        try:
            if image_upload:
                if not str(image_upload.content_type or '').startswith('image/'):
                    raise ValueError('Selecione um arquivo de imagem válido.')
                if image_upload.size > 20 * 1024 * 1024:
                    raise ValueError('A imagem deve ter no máximo 20 MB.')
                try:
                    Image.open(image_upload).verify()
                    image_upload.seek(0)
                except (UnidentifiedImageError, OSError, ValueError) as exc:
                    raise ValueError('A imagem enviada está corrompida ou não é suportada.') from exc
            if not overlay.is_required and not OverlayTimelineService.has_renderable_content(
                overlay.overlay_type, content, image_upload or overlay.image_file,
            ):
                overlay.content = content
            else:
                overlay.content = OverlayTimelineService.validate_content(
                    overlay.overlay_type, overlay.content_schema, content,
                    image_upload or overlay.image_file,
                )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            update_fields = ['content', 'update_at']
            if image_upload:
                overlay.image_file.save(image_upload.name, image_upload, save=False)
                update_fields.append('image_file')
            overlay.save(update_fields=update_fields)
            messages.success(request, 'Conteúdo visual salvo.')
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectPreviewOverlayView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, overlay_id=None):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        payload = json.loads(request.body or '{}')
        create = overlay_id is None
        if create:
            overlay_id = f'manual-{uuid.uuid4()}'
        try:
            preset_code = payload.get('preset')
            if preset_code:
                preset = OverlayPreset.objects.filter(code=preset_code, is_active=True).first()
                if not preset:
                    raise ValueError('Preset de overlay não encontrado.')
                payload['type'] = payload.get('type') or preset.overlay_type
                payload['style'] = {**preset.style, **(payload.get('style') or {})}
                payload['position'] = {**preset.position, **(payload.get('position') or {})}
                payload['animation'] = {**preset.animation, **(payload.get('animation') or {})}
            if create:
                payload['content'] = OverlayTimelineService.validate_content(
                    str(payload.get('type') or 'TEXT').upper(), {}, payload.get('content') or {},
                )
            if not create and not payload.get('delete'):
                current = project.current_timeline_revision
                target = next(
                    (item for item in ((current.timeline if current else {}).get('overlays') or []) if item.get('id') == overlay_id),
                    None,
                )
                if target and 'content' in payload:
                    payload['content'] = OverlayTimelineService.validate_content(
                        target.get('type'), target.get('content_schema') or {}, payload['content'],
                        target.get('image_storage_name'),
                    )
            revision = TimelineRevisionService.mutate_overlay(
                project, self.member, overlay_id, payload,
                create=create, delete=bool(payload.get('delete')),
            )
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        return JsonResponse({'revision': revision.revision, 'timeline': revision.timeline, 'overlay_id': overlay_id})


class ExternalMediaProjectPreviewOverlayAssetView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, overlay_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        revision = project.current_timeline_revision or TimelineRevisionService.ensure_initial(project, self.member)
        overlay = next(
            (item for item in (revision.timeline.get('overlays') or []) if item.get('id') == overlay_id),
            None,
        )
        if not overlay:
            raise Http404('Overlay não encontrado.')
        width = project.template_version.preset.width or 1920
        height = project.template_version.preset.height or 1080
        with TemporaryDirectory(prefix='connect-overlay-preview-') as temp:
            output = Path(temp) / 'overlay.png'
            OverlayAssetRenderer().render(overlay, width, height, output)
            return HttpResponse(output.read_bytes(), content_type='image/png')


class ExternalMediaProjectPreviewSessionView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        payload = json.loads(request.body or '{}')
        session = TimelineRevisionService.session(project, self.member, project.current_timeline_revision)
        session.position_ms = max(0, int(payload.get('position_ms') or 0))
        session.filters = payload.get('filters') or session.filters
        session.ui_state = payload.get('ui_state') or session.ui_state
        session.last_seen_at = timezone.now()
        session.save(update_fields=['position_ms', 'filters', 'ui_state', 'last_seen_at', 'update_at'])
        return JsonResponse({'saved': True})


class ExternalMediaProjectPreviewHistoryView(ExternalMediaRequiredMixin, View):
    direction = 'undo'

    def post(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        revision = TimelineRevisionService.navigate_history(project, self.member, self.direction)
        return JsonResponse({'revision': revision.revision, 'timeline': revision.timeline})


class ExternalMediaProjectPreviewRedoView(ExternalMediaProjectPreviewHistoryView):
    direction = 'redo'


class ExternalMediaProjectPreviewApproveView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        revision = TimelineRevisionService.approve(project, self.member)
        project.status = ExternalMediaProject.Status.PENDING
        project.progress = 68
        project.current_step = 'Renderização aprovada e adicionada à fila'
        project.save(update_fields=['status', 'progress', 'current_step', 'update_at'])
        task_id = str(uuid.uuid4())
        project.celery_task_id = task_id
        project.save(update_fields=['celery_task_id', 'update_at'])
        try:
            render_external_media_project.apply_async(args=[project.pk], task_id=task_id)
        except Exception:
            project.status = ExternalMediaProject.Status.ERROR
            project.error_message = 'Não foi possível acessar a fila de renderização.'
            project.save(update_fields=['status', 'error_message', 'update_at'])
            return JsonResponse({'error': project.error_message}, status=503)
        return JsonResponse({
            'approved_revision': revision.revision,
            'redirect_url': reverse('external_media_project_detail', kwargs={'public_id': project.public_id}),
        })


class ExternalMediaProjectStatusView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        elapsed_seconds, estimated_remaining_seconds, estimate_source = project_processing_estimate(project)
        steps = list(project.pipeline_steps.order_by('order', 'pk').values(
            'code', 'label', 'status', 'progress', 'message',
        ))
        response = JsonResponse({
            'status': project.status,
            'status_label': project.get_status_display(),
            'progress': project.progress,
            'current_step': project.current_step,
            'duration_label': project.duration_label,
            'elapsed_seconds': elapsed_seconds,
            'estimated_remaining_seconds': estimated_remaining_seconds,
            'estimate_source': estimate_source,
            'steps': steps,
            'error': project.error_message,
            'is_terminal': project.status in {
                ExternalMediaProject.Status.DRAFT,
                ExternalMediaProject.Status.AWAITING_REVIEW,
                ExternalMediaProject.Status.FINISHED,
                ExternalMediaProject.Status.ERROR,
                ExternalMediaProject.Status.CANCELLED,
            },
            'detail_url': reverse('external_media_project_detail', kwargs={'public_id': project.public_id}),
        })
        # O navegador não pode reutilizar uma resposta antiga deste endpoint: ele é
        # consultado enquanto o worker atualiza o projeto em segundo plano.
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response


class ExternalMediaProjectDuplicateView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        source = get_object_or_404(ExternalMediaProject, public_id=public_id)
        duplicate = ExternalMediaProject.objects.create(
            name=f'Cópia de {source.name}',
            template_version=source.template_version,
            created_by=self.member,
            configuration=dict(source.configuration),
        )
        messages.success(request, 'Projeto duplicado. Os uploads começam vazios.')
        return redirect('external_media_project_detail', public_id=duplicate.public_id)


class ExternalMediaProjectPremiereExportView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        project = get_object_or_404(
            ExternalMediaProject.objects.select_related('render_job'), public_id=public_id,
        )
        if not project.render_job_id or project.status != ExternalMediaProject.Status.FINISHED:
            messages.error(request, 'Finalize o processamento antes de exportar para o Premiere.')
            return redirect('external_media_project_detail', public_id=public_id)
        active = project.exports.exclude(status__in=[
            ExternalMediaProjectExport.Status.FINISHED,
            ExternalMediaProjectExport.Status.ERROR,
        ]).first()
        if active:
            messages.warning(request, 'Já existe uma exportação deste projeto em andamento.')
            return redirect('external_media_project_detail', public_id=public_id)
        export = ExternalMediaProjectExport.objects.create(
            project=project,
            created_by=self.member,
            status=ExternalMediaProjectExport.Status.PREPARING,
            progress=2,
            current_step='Exportação adicionada à fila',
        )
        try:
            result = export_premiere_project.delay(export.pk)
            export.celery_task_id = result.id
            export.save(update_fields=['celery_task_id', 'update_at'])
            messages.success(request, 'A exportação editável foi iniciada em segundo plano.')
        except Exception:
            export.status = ExternalMediaProjectExport.Status.ERROR
            export.error_message = 'Não foi possível acessar a fila. Verifique o Redis e o worker.'
            export.save(update_fields=['status', 'error_message', 'update_at'])
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectExportStatusView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, export_public_id):
        item = get_object_or_404(
            ExternalMediaProjectExport, public_id=export_public_id, project__public_id=public_id,
        )
        return JsonResponse({
            'status': item.status,
            'status_label': item.get_status_display(),
            'progress': item.progress,
            'current_step': item.current_step,
            'duration_label': item.duration_label,
            'error': item.error_message,
            'is_terminal': item.status in {
                ExternalMediaProjectExport.Status.FINISHED,
                ExternalMediaProjectExport.Status.ERROR,
            },
        })


class ExternalMediaProjectExportDownloadView(ExternalMediaRequiredMixin, View):
    field_name = 'archive'

    def get(self, request, public_id, export_public_id):
        item = get_object_or_404(
            ExternalMediaProjectExport, public_id=export_public_id, project__public_id=public_id,
        )
        field = getattr(item, self.field_name)
        if not field:
            raise Http404('O arquivo desta exportação ainda não está disponível.')
        if self.field_name == 'archive':
            ExternalMediaProjectExport.objects.filter(pk=item.pk).update(
                download_count=F('download_count') + 1,
            )
        return protected_file_response(request, field)


class ExternalMediaProjectTimelineDownloadView(ExternalMediaProjectExportDownloadView):
    field_name = 'timeline_json'


class ExternalMediaDetailView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    @staticmethod
    def _can_retry_render(job):
        available_languages = set(job.subtitle_tracks.values_list('language', flat=True))
        return set(job.output_languages).issubset(available_languages)

    def get(self, request, public_id):
        job = get_object_or_404(
            ExternalMediaJob.objects.select_related('created_by', 'preset', 'subtitle_style')
            .prefetch_related('assets', 'subtitle_tracks'),
            public_id=public_id,
        )
        return render(request, 'member/external_media/detail.html', self.media_context(
            job=job,
            assets=job.assets.all(),
            can_retry_render=self._can_retry_render(job),
        ))


class ExternalMediaStatusView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id):
        job = get_object_or_404(ExternalMediaJob, public_id=public_id)
        return JsonResponse({
            'status': job.status,
            'status_label': job.get_status_display(),
            'progress': job.progress,
            'current_step': job.current_step,
            'duration_label': job.duration_label,
            'error': job.error_message,
            'is_terminal': job.status in {
                ExternalMediaJob.Status.FINISHED,
                ExternalMediaJob.Status.ERROR,
                ExternalMediaJob.Status.CANCELLED,
            },
            'detail_url': reverse('external_media_detail', kwargs={'public_id': job.public_id}),
        })


class ExternalMediaEditorView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    editable_statuses = {
        ExternalMediaJob.Status.FINISHED,
        ExternalMediaJob.Status.ERROR,
    }

    def get_job(self, public_id):
        return get_object_or_404(
            ExternalMediaJob.objects.select_related(
                'preset',
                'subtitle_style',
                'translated_subtitle_style',
            ).prefetch_related('subtitle_tracks__cues'),
            public_id=public_id,
        )

    def get(self, request, public_id):
        job = self.get_job(public_id)
        if job.status not in self.editable_statuses:
            messages.warning(request, 'As legendas ainda não estão disponíveis para edição.')
            return redirect('external_media_detail', public_id=public_id)
        return render(request, 'member/external_media/editor.html', self.media_context(
            job=job,
            tracks=job.subtitle_tracks.all(),
        ))

    def post(self, request, public_id):
        job = self.get_job(public_id)
        if job.status not in self.editable_statuses:
            raise Http404
        cues = SubtitleCue.objects.filter(track__job=job)
        changed = []
        for cue in cues:
            key = f'cue_{cue.pk}'
            if key not in request.POST:
                continue
            text = request.POST[key].strip()
            if not text:
                messages.error(request, f'O bloco {cue.cue_index} não pode ficar vazio.')
                return redirect('external_media_editor', public_id=public_id)
            if cue.text != text:
                cue.text = text
                changed.append(cue)
        if changed:
            SubtitleCue.objects.bulk_update(changed, ['text'])
            changed_track_ids = {cue.track_id for cue in changed}
            for track in SubtitleTrack.objects.filter(pk__in=changed_track_ids):
                track.revision += 1
                track.human_reviewed = True
                track.subtitle_dirty = True
                track.save(update_fields=['revision', 'human_reviewed', 'subtitle_dirty', 'update_at'])
                SubtitleReviewService.ensure_revision_snapshot(
                    track, member=self.member, reason='Edição interna',
                )
                if track.is_source:
                    SubtitleTrack.objects.filter(job=job).exclude(pk=track.pk).update(
                        translation_status=SubtitleTrack.TranslationStatus.SOURCE_CHANGED,
                    )
        messages.success(request, 'Textos salvos. Aplique as alterações ao vídeo quando terminar a revisão.')
        return redirect('external_media_editor', public_id=public_id)


class ExternalMediaRenderView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        with transaction.atomic():
            job = get_object_or_404(
                ExternalMediaJob.objects.select_for_update(),
                public_id=public_id,
            )
            if job.status != ExternalMediaJob.Status.ERROR:
                messages.warning(request, 'Este processamento não está pronto para renderização.')
                return redirect('external_media_detail', public_id=public_id)
            job.status = ExternalMediaJob.Status.PENDING
            job.progress = 84
            job.current_step = 'Renderização adicionada à fila'
            job.error_message = ''
            job.save(update_fields=['status', 'progress', 'current_step', 'error_message', 'update_at'])
            transaction.on_commit(lambda: self._enqueue(job.pk))
        messages.success(request, 'Renderização iniciada em segundo plano.')
        return redirect('external_media_detail', public_id=public_id)

    @staticmethod
    def _enqueue(job_id):
        try:
            result = render_external_media.delay(job_id)
            ExternalMediaJob.objects.filter(pk=job_id).update(celery_task_id=result.id)
        except Exception:
            ExternalMediaJob.objects.filter(pk=job_id).update(
                status=ExternalMediaJob.Status.ERROR,
                current_step='Não foi possível acessar a fila de renderização',
                error_message='Verifique se o Redis e o worker Celery estão ativos.',
            )


class ExternalMediaRetryView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        with transaction.atomic():
            job = get_object_or_404(
                ExternalMediaJob.objects.select_for_update(),
                public_id=public_id,
            )
            if job.status != ExternalMediaJob.Status.ERROR:
                messages.warning(request, 'Este processamento não está em erro no momento.')
                return redirect('external_media_detail', public_id=public_id)
            job.status = ExternalMediaJob.Status.PENDING
            job.progress = 5
            job.current_step = 'Processamento adicionado novamente à fila'
            job.error_message = ''
            job.save(update_fields=['status', 'progress', 'current_step', 'error_message', 'update_at'])
            transaction.on_commit(lambda: self._enqueue(job.pk))
        messages.success(request, 'Processamento reenviado para a fila.')
        return redirect('external_media_detail', public_id=public_id)

    @staticmethod
    def _enqueue(job_id):
        try:
            result = prepare_external_media.delay(job_id)
            ExternalMediaJob.objects.filter(pk=job_id).update(celery_task_id=result.id)
        except Exception:
            ExternalMediaJob.objects.filter(pk=job_id).update(
                status=ExternalMediaJob.Status.ERROR,
                current_step='Não foi possível acessar a fila de processamento',
                error_message='Verifique se o Redis e o worker Celery estão ativos.',
            )


class ExternalMediaAssetDownloadView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id, asset_id):
        asset = get_object_or_404(MediaAsset, pk=asset_id, job__public_id=public_id)
        if request.GET.get('preview') != '1':
            MediaAsset.objects.filter(pk=asset.pk).update(download_count=F('download_count') + 1)
        return protected_file_response(request, asset.file)


class ExternalMediaOriginalDownloadView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id):
        job = get_object_or_404(ExternalMediaJob, public_id=public_id)
        return protected_file_response(request, job.original_video)


class ExternalMediaGlossaryView(ExternalMediaRequiredMixin, ExternalMediaContextMixin, View):
    def get(self, request):
        return render(request, 'member/external_media/glossary.html', self.media_context(
            form=GlossaryTermForm(),
            terms=GlossaryTerm.objects.filter(is_active=True),
        ))

    def post(self, request):
        form = GlossaryTermForm(request.POST)
        if form.is_valid():
            # Older glossary deletions were soft-deletes. Remove a matching
            # tombstone before inserting, so a user can recreate a term that was
            # deliberately deleted without tripping the database unique key.
            GlossaryTerm.all_objects.filter(
                source_language=form.cleaned_data['source_language'],
                target_language=form.cleaned_data['target_language'],
                source_text=form.cleaned_data['source_text'],
                deleted__isnull=False,
            ).delete(force_policy=HARD_DELETE)
            try:
                form.save()
            except IntegrityError:
                # The form catches normal duplicates. This protects against two
                # simultaneous submissions reaching the database at once.
                form.add_error(
                    'source_text',
                    'Este termo já existe para este par de idiomas. Edite ou remova o termo existente.',
                )
            else:
                messages.success(request, 'Termo adicionado ao glossário.')
                return redirect('external_media_glossary')
        return render(request, 'member/external_media/glossary.html', self.media_context(
            form=form,
            terms=GlossaryTerm.objects.filter(is_active=True),
        ))


class ExternalMediaGlossaryDeleteView(ExternalMediaRequiredMixin, View):
    def post(self, request, term_id):
        term = get_object_or_404(GlossaryTerm, pk=term_id)
        # Glossary terms have no processing history to preserve. A real delete
        # means the same language pair/term can be created again immediately.
        term.delete(force_policy=HARD_DELETE)
        messages.success(request, 'Termo removido do glossário.')
        return redirect('external_media_glossary')
