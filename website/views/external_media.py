import mimetypes
import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import F, Max, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views import View

from ..external_media.tasks import (
    analyze_video_mastering,
    export_premiere_project,
    master_video,
    prepare_external_media,
    render_external_media,
    render_external_media_project,
    run_external_media_project,
)
from ..forms.external_media import (
    ExternalMediaJobForm,
    ExternalMediaProjectForm,
    ExternalMediaProjectSettingsForm,
    GlossaryTermForm,
    ProjectBlockMediaForm,
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
    ProjectBlockMedia,
    SubtitleCue,
    VideoMasteringJob,
)
from ..external_media.services import ProjectService
from .mixins import ExternalMediaRequiredMixin


_RANGE_RE = re.compile(r'bytes=(\d*)-(\d*)')


def _file_chunks(file_handle, start, length, block_size=8192):
    file_handle.seek(start)
    remaining = length
    while remaining > 0:
        chunk = file_handle.read(min(block_size, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        yield chunk


def protected_file_response(request, field_file):
    """Serve locally (with HTTP Range for video seek) or redirect to a signed S3 URL."""
    filename = Path(field_file.name).name
    preview = request.GET.get('preview') == '1'
    content_type = mimetypes.guess_type(field_file.name)[0] or 'application/octet-stream'
    if settings.USE_S3:
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
        jobs = ExternalMediaJob.objects.select_related('created_by', 'preset').prefetch_related('assets')
        summary = jobs.aggregate(total=Sum('assets__download_count'))

        legacy_jobs = jobs.filter(project__isnull=True).order_by('-created_at')
        if query:
            legacy_jobs = legacy_jobs.filter(
                Q(name__icontains=query) | Q(preset__name__icontains=query),
            )

        return render(request, 'member/external_media/dashboard.html', self.media_context(
            query=query,
            status=status,
            total=projects.count(),
            projects=projects[:50],
            legacy_jobs=legacy_jobs[:20],
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
                'block_media', 'pipeline_steps', 'render_job__assets',
            ),
            public_id=public_id,
        )

    def get(self, request, public_id):
        project = self.get_project(public_id)
        media_by_block = {}
        missing_media_by_block = {}
        for item in project.block_media.all():
            if ProjectService.file_exists(item.file):
                media_by_block.setdefault(item.block_id, []).append(item)
            else:
                missing_media_by_block.setdefault(item.block_id, []).append(item)
        blocks = [
            {
                'definition': block,
                'media': media_by_block.get(block.pk, []),
                'missing_media': missing_media_by_block.get(block.pk, []),
                'has_default_video': ProjectService.file_exists(block.default_video),
            }
            for block in project.template_version.blocks.all()
        ]
        return render(request, 'member/external_media/project_detail.html', self.media_context(
            project=project,
            blocks=blocks,
            plugins=project.template_version.plugins.all(),
            steps=project.pipeline_steps.all(),
            upload_form=ProjectBlockMediaForm(),
            settings_form=ExternalMediaProjectSettingsForm(project=project),
            assets=project.render_job.assets.all() if project.render_job_id else [],
            validation_errors=ProjectService.validate_uploads(project),
            can_retry_render=self._can_retry_project_render(project),
            project_exports=project.exports.order_by('-created_at')[:10],
        ))


class ExternalMediaProjectUploadView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id, block_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        if project.status != ExternalMediaProject.Status.DRAFT:
            messages.error(request, 'Os uploads ficam bloqueados após iniciar o pipeline.')
            return redirect('external_media_project_detail', public_id=public_id)
        block = get_object_or_404(MediaTemplateBlock, pk=block_id, version=project.template_version)
        form = ProjectBlockMediaForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, 'Não foi possível enviar o vídeo: ' + ' '.join(
                error for errors in form.errors.values() for error in errors
            ))
            return redirect('external_media_project_detail', public_id=public_id)
        count = sum(
            1 for existing in project.block_media.filter(block=block)
            if ProjectService.file_exists(existing.file)
        )
        if count >= block.max_occurrences:
            messages.error(request, f'O bloco {block.name} aceita no máximo {block.max_occurrences} vídeo(s).')
            return redirect('external_media_project_detail', public_id=public_id)
        item = form.save(commit=False)
        item.project = project
        item.block = block
        item.position = (
            ProjectBlockMedia.all_objects.filter(project=project, block=block)
            .aggregate(value=Max('position'))['value'] or 0
        ) + 1
        item.original_filename = item.file.name
        item.file_size = item.file.size
        item.trim_start_ms = ProjectBlockMediaForm.seconds_to_ms(form.cleaned_data.get('trim_start_seconds')) or 0
        item.trim_end_ms = ProjectBlockMediaForm.seconds_to_ms(form.cleaned_data.get('trim_end_seconds'))
        item.save()
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


class ExternalMediaProjectRunView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        with transaction.atomic():
            project = get_object_or_404(
                ExternalMediaProject.objects.select_for_update().prefetch_related(
                    'template_version__blocks', 'block_media',
                ),
                public_id=public_id,
            )
            if project.status not in {ExternalMediaProject.Status.DRAFT, ExternalMediaProject.Status.ERROR}:
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
            project.save(update_fields=['status', 'progress', 'current_step', 'error_message', 'update_at'])
            transaction.on_commit(lambda: self._enqueue(project.pk))
        messages.success(request, 'Pipeline iniciado em segundo plano.')
        return redirect('external_media_project_detail', public_id=public_id)

    @staticmethod
    def _enqueue(project_id):
        try:
            result = run_external_media_project.delay(project_id)
            ExternalMediaProject.objects.filter(pk=project_id).update(celery_task_id=result.id)
        except Exception:
            ExternalMediaProject.objects.filter(pk=project_id).update(
                status=ExternalMediaProject.Status.ERROR,
                current_step='Não foi possível acessar a fila de processamento',
                error_message='Verifique se o Redis e o worker Celery estão ativos.',
            )


class ExternalMediaProjectRenderView(ExternalMediaRequiredMixin, View):
    def post(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        if project.status != ExternalMediaProject.Status.ERROR:
            messages.warning(request, 'O projeto ainda não está pronto para renderização.')
            return redirect('external_media_project_detail', public_id=public_id)
        project.status = ExternalMediaProject.Status.PENDING
        project.progress = 84
        project.current_step = 'Renderização adicionada à fila'
        project.error_message = ''
        project.save(update_fields=['status', 'progress', 'current_step', 'error_message', 'update_at'])
        try:
            result = render_external_media_project.delay(project.pk)
            project.celery_task_id = result.id
            project.save(update_fields=['celery_task_id', 'update_at'])
        except Exception:
            project.status = ExternalMediaProject.Status.ERROR
            project.error_message = 'Verifique se o Redis e o worker Celery estão ativos.'
            project.save(update_fields=['status', 'error_message', 'update_at'])
        return redirect('external_media_project_detail', public_id=public_id)


class ExternalMediaProjectStatusView(ExternalMediaRequiredMixin, View):
    def get(self, request, public_id):
        project = get_object_or_404(ExternalMediaProject, public_id=public_id)
        return JsonResponse({
            'status': project.status,
            'status_label': project.get_status_display(),
            'progress': project.progress,
            'current_step': project.current_step,
            'duration_label': project.duration_label,
            'error': project.error_message,
            'is_terminal': project.status in {
                ExternalMediaProject.Status.DRAFT,
                ExternalMediaProject.Status.FINISHED,
                ExternalMediaProject.Status.ERROR,
                ExternalMediaProject.Status.CANCELLED,
            },
            'detail_url': reverse('external_media_project_detail', kwargs={'public_id': project.public_id}),
        })


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
        if job.status == ExternalMediaJob.Status.FINISHED:
            project = getattr(job, 'project', None)
            job.status = ExternalMediaJob.Status.PENDING
            job.progress = 84
            job.current_step = 'Renderização adicionada à fila'
            job.save(update_fields=['status', 'progress', 'current_step', 'update_at'])
            if project:
                project.status = ExternalMediaProject.Status.PENDING
                project.progress = 84
                project.current_step = 'Renderização adicionada à fila'
                project.error_message = ''
                project.save(update_fields=['status', 'progress', 'current_step', 'error_message', 'update_at'])
                project_id = project.pk
                transaction.on_commit(lambda: render_external_media_project.delay(project_id))
            else:
                job_id = job.pk
                transaction.on_commit(lambda: render_external_media.delay(job_id))
        messages.success(request, 'Textos salvos. Os timestamps permaneceram inalterados.')
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
            form.save()
            messages.success(request, 'Termo adicionado ao glossário.')
            return redirect('external_media_glossary')
        return render(request, 'member/external_media/glossary.html', self.media_context(
            form=form,
            terms=GlossaryTerm.objects.filter(is_active=True),
        ))


class ExternalMediaGlossaryDeleteView(ExternalMediaRequiredMixin, View):
    def post(self, request, term_id):
        term = get_object_or_404(GlossaryTerm, pk=term_id)
        term.delete()
        messages.success(request, 'Termo removido do glossário.')
        return redirect('external_media_glossary')
