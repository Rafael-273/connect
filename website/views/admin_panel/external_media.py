import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views import View

from ...forms.admin_external_media import (
    AdminBackgroundMusicForm,
    AdminMediaTemplateBlockFormSet,
    AdminMediaTemplateForm,
    AdminMediaTemplateVersionForm,
    AdminRenderPresetForm,
    AdminSubtitleStyleForm,
)
from ...models.music import Music
from ...models.external_media import (
    ExternalMediaJob,
    MediaTemplate,
    MediaTemplateVersion,
    RenderPreset,
    SubtitleStyle,
)
from ..mixins import AdminRequiredMixin


class AdminExternalMediaTemplateListView(LoginRequiredMixin, AdminRequiredMixin, View):
    def get(self, request):
        search = request.GET.get('search', '').strip()
        status = request.GET.get('status', '').strip()

        templates = MediaTemplate.objects.all()
        if search:
            templates = templates.filter(Q(name__icontains=search) | Q(description__icontains=search))
        if status == 'active':
            templates = templates.filter(is_active=True)
        elif status == 'inactive':
            templates = templates.filter(is_active=False)

        templates = list(templates.order_by('name'))
        for template in templates:
            current_version = template.current_version
            template.blocks_count = current_version.blocks.count() if current_version else 0
            template.projects_count = current_version.projects.count() if current_version else 0

        return render(request, 'admin_panel/external_media/templates.html', {
            'templates': templates,
            'search': search,
            'status': status,
            'stats': {
                'total': MediaTemplate.objects.count(),
                'active': MediaTemplate.objects.filter(is_active=True).count(),
                'projects': MediaTemplateVersion.objects.aggregate(total=Count('projects', distinct=True))['total'],
            },
        })


def get_or_create_current_media_version(template):
    current = template.current_version
    if current:
        return current
    preset = RenderPreset.objects.filter(is_active=True).order_by('name').first() or RenderPreset.objects.order_by('name').first()
    if not preset:
        return None
    style = SubtitleStyle.objects.filter(is_active=True).order_by('name').first()
    return MediaTemplateVersion.objects.create(
        template=template,
        version=1,
        status=MediaTemplateVersion.Status.PUBLISHED,
        published_at=timezone.now(),
        preset=preset,
        subtitle_style=style,
        translated_subtitle_style=style,
        original_language='pt',
        output_languages=['pt'],
        default_settings={'language_mode': 'single', 'spoken_languages': ['pt'], 'translated_language': ''},
    )


def delete_subtitle_style(style):
    replacement = (
        SubtitleStyle.objects.filter(is_active=True)
        .exclude(pk=style.pk)
        .order_by('name')
        .first()
    )
    jobs_using_style = ExternalMediaJob.objects.filter(subtitle_style=style).exists()
    if jobs_using_style and not replacement:
        return False, (
            f'Estilo "{style.name}" não pode ser removido porque é o único disponível '
            'e ainda está vinculado a processamentos antigos.'
        )

    with transaction.atomic():
        MediaTemplateVersion.objects.filter(translated_subtitle_style=style).update(
            translated_subtitle_style=None,
        )
        MediaTemplateVersion.objects.filter(subtitle_style=style).update(
            subtitle_style=replacement,
        )
        ExternalMediaJob.objects.filter(translated_subtitle_style=style).update(
            translated_subtitle_style=None,
        )
        if replacement:
            ExternalMediaJob.objects.filter(subtitle_style=style).update(
                subtitle_style=replacement,
            )
        style.delete()
    return True, replacement


class AdminExternalMediaTemplateEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    def get(self, request, template_id):
        template = get_object_or_404(MediaTemplate, id=template_id)
        version = get_or_create_current_media_version(template)
        if version is None:
            messages.warning(request, 'Cadastre um preset de renderização antes de editar templates de mídia.')
            return redirect('admin_external_media_template_detail', template_id=template.id)
        return redirect('admin_external_media_version_edit', version_id=version.id)

    def post(self, request, template_id):
        return self.get(request, template_id)


class AdminExternalMediaTemplateDetailView(LoginRequiredMixin, AdminRequiredMixin, View):
    def get(self, request, template_id):
        template = get_object_or_404(MediaTemplate, id=template_id)
        current_version = get_or_create_current_media_version(template)
        if current_version is None:
            messages.warning(request, 'Cadastre um preset de renderização para configurar este template.')
        if current_version:
            current_version = (
                MediaTemplateVersion.objects.select_related('preset', 'subtitle_style', 'translated_subtitle_style')
                .annotate(
                    blocks_count=Count('blocks', distinct=True),
                    plugins_count=Count('plugins', distinct=True),
                    projects_count=Count('projects', distinct=True),
                )
                .get(pk=current_version.pk)
            )
        return render(request, 'admin_panel/external_media/template_detail.html', {
            'template': template,
            'current_version': current_version,
        })


class AdminExternalMediaVersionFormView(LoginRequiredMixin, AdminRequiredMixin, View):
    template_name = 'admin_panel/external_media/version_form.html'

    def get(self, request, template_id=None, version_id=None):
        template, version, is_create = self._resolve_objects(template_id, version_id)
        if is_create and template is None:
            messages.warning(request, 'Cadastre um preset de renderização antes de criar templates de mídia.')
            return redirect('admin_external_media_templates')
        return render(request, self.template_name, self._context(template, version, is_create=is_create))

    def post(self, request, template_id=None, version_id=None):
        template, version, is_create = self._resolve_objects(template_id, version_id)
        if is_create and template is None:
            messages.warning(request, 'Cadastre um preset de renderização antes de criar templates de mídia.')
            return redirect('admin_external_media_templates')

        template_form = AdminMediaTemplateForm(
            request.POST,
            request.FILES,
            instance=template if template.pk else None,
        )
        form = AdminMediaTemplateVersionForm(request.POST, request.FILES, instance=version)
        block_formset = AdminMediaTemplateBlockFormSet(
            request.POST, request.FILES, instance=version, prefix='blocks',
        )

        if template_form.is_valid() and form.is_valid() and block_formset.is_valid():
            with transaction.atomic():
                saved_template = template_form.save()
                saved_version = form.save(commit=False)
                saved_version.template = saved_template
                saved_version.version = version.version or 1
                saved_version.status = MediaTemplateVersion.Status.PUBLISHED
                saved_version.published_at = saved_version.published_at or timezone.now()
                saved_version.save()
                block_formset.instance = saved_version
                block_formset.save()
                form.sync_advanced_plugins(saved_version)
            action = 'criado' if is_create else 'atualizado'
            messages.success(request, f'Template "{saved_template.name}" {action}.')
            return redirect('admin_external_media_template_detail', template_id=saved_template.id)

        messages.error(request, 'Revise o template e os blocos.')
        return render(
            request,
            self.template_name,
            self._context(template, version, form, block_formset, template_form, is_create=is_create),
        )

    @staticmethod
    def _default_preset_and_style():
        preset = (
            RenderPreset.objects.filter(is_active=True).order_by('name').first()
            or RenderPreset.objects.order_by('name').first()
        )
        style = SubtitleStyle.objects.filter(is_active=True).order_by('name').first()
        return preset, style

    @staticmethod
    def _new_template_and_version():
        preset, style = AdminExternalMediaVersionFormView._default_preset_and_style()
        if not preset:
            return None, None
        template = MediaTemplate()
        version = MediaTemplateVersion(
            preset=preset,
            subtitle_style=style,
            translated_subtitle_style=style,
            original_language='pt',
            output_languages=['pt'],
            default_settings={
                'language_mode': 'translated',
                'spoken_languages': ['pt'],
                'translated_language': '',
            },
            version=1,
            status=MediaTemplateVersion.Status.PUBLISHED,
        )
        return template, version

    @staticmethod
    def _resolve_objects(template_id=None, version_id=None):
        if version_id:
            version = get_object_or_404(MediaTemplateVersion.objects.select_related('template'), id=version_id)
            return version.template, version, False
        if template_id:
            template = get_object_or_404(MediaTemplate, id=template_id)
            version = get_or_create_current_media_version(template)
            if version is None:
                raise Http404('Cadastre um preset de renderização antes de configurar templates de mídia.')
            return template, version, False
        template, version = AdminExternalMediaVersionFormView._new_template_and_version()
        if template is None:
            return None, None, True
        return template, version, True

    def _context(
        self,
        template,
        version,
        form=None,
        block_formset=None,
        template_form=None,
        is_create=False,
    ):
        if template_form is None:
            template_form = AdminMediaTemplateForm(instance=template if template.pk else None)
        if form is None:
            form = AdminMediaTemplateVersionForm(instance=version)
        if block_formset is None:
            block_formset = AdminMediaTemplateBlockFormSet(instance=version, prefix='blocks')
        render_presets = list(RenderPreset.objects.order_by('name'))
        subtitle_styles = list(SubtitleStyle.objects.order_by('name'))
        background_musics = list(Music.objects.order_by('name', 'singer'))
        if is_create:
            title = 'Novo Template de Mídia'
        elif template.pk:
            title = f'Editar Template: {template.name}'
        else:
            title = 'Novo Template de Mídia'
        return {
            'template': template,
            'version': version,
            'template_form': template_form,
            'form': form,
            'block_formset': block_formset,
            'is_create': is_create,
            'render_presets': render_presets,
            'render_preset_rows': [
                {'preset': preset, 'extra_args_json': json.dumps(preset.extra_ffmpeg_args or [])}
                for preset in render_presets
            ],
            'subtitle_styles': subtitle_styles,
            'background_music_rows': background_musics,
            'preset_form': AdminRenderPresetForm(prefix='preset'),
            'style_form': AdminSubtitleStyleForm(prefix='style'),
            'music_form': AdminBackgroundMusicForm(prefix='bgmusic'),
            'title': title,
        }


class AdminExternalMediaVersionPublishView(LoginRequiredMixin, AdminRequiredMixin, View):
    def post(self, request, version_id):
        version = get_object_or_404(MediaTemplateVersion.objects.select_related('template'), id=version_id)
        version.status = MediaTemplateVersion.Status.PUBLISHED
        version.published_at = version.published_at or timezone.now()
        version.save(update_fields=['status', 'published_at', 'update_at'])
        messages.success(request, f'Template "{version.template.name}" atualizado.')
        return redirect('admin_external_media_template_detail', template_id=version.template_id)


class AdminExternalMediaVersionDuplicateView(LoginRequiredMixin, AdminRequiredMixin, View):
    def post(self, request, version_id):
        version = get_object_or_404(MediaTemplateVersion.objects.select_related('template'), id=version_id)
        messages.info(request, 'A criação de versões foi removida. Edite o template atual diretamente.')
        return redirect('admin_external_media_version_edit', version_id=version.id)


class AdminExternalMediaAssetRedirectMixin:
    @staticmethod
    def _redirect_back(request):
        target = request.POST.get('next') or request.META.get('HTTP_REFERER') or ''
        if target and url_has_allowed_host_and_scheme(
            target,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(target)
        return redirect('admin_external_media_templates')


class AdminExternalMediaPresetSaveView(LoginRequiredMixin, AdminRequiredMixin, AdminExternalMediaAssetRedirectMixin, View):
    def post(self, request):
        preset_id = request.POST.get('preset_id')
        instance = RenderPreset.objects.filter(id=preset_id).first() if preset_id else None
        form = AdminRenderPresetForm(request.POST, instance=instance, prefix='preset')
        if form.is_valid():
            preset = form.save()
            action = 'atualizado' if instance else 'criado'
            messages.success(request, f'Preset "{preset.name}" {action}.')
        else:
            messages.error(request, self._errors_to_text(form))
        return self._redirect_back(request)

    @staticmethod
    def _errors_to_text(form):
        errors = []
        for field, field_errors in form.errors.items():
            label = form.fields[field].label if field in form.fields else field
            errors.append(f'{label}: {field_errors[0]}')
        return 'Revise o preset. ' + ' '.join(errors)


class AdminExternalMediaPresetDeleteView(LoginRequiredMixin, AdminRequiredMixin, AdminExternalMediaAssetRedirectMixin, View):
    def post(self, request, preset_id):
        preset = get_object_or_404(RenderPreset, id=preset_id)
        name = preset.name
        try:
            preset.delete()
            messages.success(request, f'Preset "{name}" removido.')
        except ProtectedError:
            preset.is_active = False
            preset.save(update_fields=['is_active', 'update_at'])
            messages.warning(request, f'Preset "{name}" está em uso e foi desativado.')
        return self._redirect_back(request)


class AdminExternalMediaSubtitleStyleSaveView(LoginRequiredMixin, AdminRequiredMixin, AdminExternalMediaAssetRedirectMixin, View):
    def post(self, request):
        style_id = request.POST.get('style_id')
        instance = SubtitleStyle.objects.filter(id=style_id).first() if style_id else None
        form = AdminSubtitleStyleForm(request.POST, instance=instance, prefix='style')
        if form.is_valid():
            style = form.save()
            action = 'atualizado' if instance else 'criado'
            messages.success(request, f'Estilo "{style.name}" {action}.')
        else:
            messages.error(request, self._errors_to_text(form))
        return self._redirect_back(request)

    @staticmethod
    def _errors_to_text(form):
        errors = []
        for field, field_errors in form.errors.items():
            label = form.fields[field].label if field in form.fields else field
            errors.append(f'{label}: {field_errors[0]}')
        return 'Revise o estilo. ' + ' '.join(errors)


class AdminExternalMediaSubtitleStyleDeleteView(LoginRequiredMixin, AdminRequiredMixin, AdminExternalMediaAssetRedirectMixin, View):
    def post(self, request, style_id):
        style = get_object_or_404(SubtitleStyle, id=style_id)
        name = style.name
        deleted, result = delete_subtitle_style(style)
        if deleted:
            if result:
                messages.success(
                    request,
                    f'Estilo "{name}" removido. Templates e processamentos antigos passaram a usar "{result.name}".',
                )
            else:
                messages.success(request, f'Estilo "{name}" removido.')
        else:
            messages.error(request, result)
        return self._redirect_back(request)


class AdminExternalMediaBackgroundMusicSaveView(LoginRequiredMixin, AdminRequiredMixin, AdminExternalMediaAssetRedirectMixin, View):
    def post(self, request):
        music_id = request.POST.get('bgmusic_id')
        instance = Music.objects.filter(id=music_id).first() if music_id else None
        form = AdminBackgroundMusicForm(request.POST, request.FILES, instance=instance, prefix='bgmusic')
        if form.is_valid():
            music = form.save()
            action = 'atualizada' if instance else 'criada'
            messages.success(request, f'Música "{music.name}" {action}.')
        else:
            messages.error(request, self._errors_to_text(form))
        return self._redirect_back(request)

    @staticmethod
    def _errors_to_text(form):
        errors = []
        for field, field_errors in form.errors.items():
            label = form.fields[field].label if field in form.fields else field
            errors.append(f'{label}: {field_errors[0]}')
        return 'Revise a música. ' + ' '.join(errors)


class AdminExternalMediaBackgroundMusicDeleteView(LoginRequiredMixin, AdminRequiredMixin, AdminExternalMediaAssetRedirectMixin, View):
    def post(self, request, music_id):
        music = get_object_or_404(Music, id=music_id)
        name = music.name
        if music.external_media_versions.exists():
            messages.warning(request, f'A música "{name}" está em uso em um template e não pode ser removida.')
            return self._redirect_back(request)
        music.delete()
        messages.success(request, f'Música "{name}" removida.')
        return self._redirect_back(request)
