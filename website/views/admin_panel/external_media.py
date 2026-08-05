import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Max, Q
from django.db.models.deletion import ProtectedError
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
    MediaTemplate,
    MediaTemplateBlock,
    MediaTemplatePlugin,
    MediaTemplateVersion,
    RenderPreset,
    SubtitleStyle,
)
from ..mixins import AdminRequiredMixin


class AdminExternalMediaTemplateListView(LoginRequiredMixin, AdminRequiredMixin, View):
    def get(self, request):
        search = request.GET.get('search', '').strip()
        status = request.GET.get('status', '').strip()

        templates = MediaTemplate.objects.annotate(
            versions_count=Count('versions', distinct=True),
            projects_count=Count('versions__projects', distinct=True),
        )
        if search:
            templates = templates.filter(Q(name__icontains=search) | Q(description__icontains=search))
        if status == 'active':
            templates = templates.filter(is_active=True)
        elif status == 'inactive':
            templates = templates.filter(is_active=False)

        return render(request, 'admin_panel/external_media/templates.html', {
            'templates': templates.order_by('name'),
            'search': search,
            'status': status,
            'stats': {
                'total': MediaTemplate.objects.count(),
                'published': MediaTemplateVersion.objects.filter(status=MediaTemplateVersion.Status.PUBLISHED).count(),
                'drafts': MediaTemplateVersion.objects.filter(status=MediaTemplateVersion.Status.DRAFT).count(),
            },
        })


class AdminExternalMediaTemplateCreateView(LoginRequiredMixin, AdminRequiredMixin, View):
    template_name = 'admin_panel/external_media/template_form.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form': AdminMediaTemplateForm(),
            'title': 'Novo Template de Midia',
        })

    def post(self, request):
        form = AdminMediaTemplateForm(request.POST, request.FILES)
        if form.is_valid():
            template = form.save()
            messages.success(request, f'Template "{template.name}" criado. Agora crie a primeira versão.')
            return redirect('admin_external_media_version_create', template_id=template.id)
        messages.error(request, 'Revise os dados do template.')
        return render(request, self.template_name, {'form': form, 'title': 'Novo Template de Midia'})


class AdminExternalMediaTemplateEditView(LoginRequiredMixin, AdminRequiredMixin, View):
    template_name = 'admin_panel/external_media/template_form.html'

    def get(self, request, template_id):
        template = get_object_or_404(MediaTemplate, id=template_id)
        return render(request, self.template_name, {
            'form': AdminMediaTemplateForm(instance=template),
            'template': template,
            'title': f'Editar Template: {template.name}',
        })

    def post(self, request, template_id):
        template = get_object_or_404(MediaTemplate, id=template_id)
        form = AdminMediaTemplateForm(request.POST, request.FILES, instance=template)
        if form.is_valid():
            form.save()
            messages.success(request, f'Template "{template.name}" atualizado.')
            return redirect('admin_external_media_template_detail', template_id=template.id)
        messages.error(request, 'Revise os dados do template.')
        return render(request, self.template_name, {
            'form': form,
            'template': template,
            'title': f'Editar Template: {template.name}',
        })


class AdminExternalMediaTemplateDetailView(LoginRequiredMixin, AdminRequiredMixin, View):
    def get(self, request, template_id):
        template = get_object_or_404(MediaTemplate, id=template_id)
        versions = template.versions.select_related('preset', 'subtitle_style', 'translated_subtitle_style').annotate(
            blocks_count=Count('blocks', distinct=True),
            plugins_count=Count('plugins', distinct=True),
            projects_count=Count('projects', distinct=True),
        )
        return render(request, 'admin_panel/external_media/template_detail.html', {
            'template': template,
            'versions': versions,
            'published_version': template.published_version,
        })


class AdminExternalMediaVersionFormView(LoginRequiredMixin, AdminRequiredMixin, View):
    template_name = 'admin_panel/external_media/version_form.html'

    def get(self, request, template_id=None, version_id=None):
        template, version = self._resolve_objects(template_id, version_id)
        if version and version.status != MediaTemplateVersion.Status.DRAFT:
            messages.info(request, 'Versões publicadas ficam congeladas. Duplique para criar uma nova versão editável.')
            return redirect('admin_external_media_template_detail', template_id=version.template_id)
        return render(request, self.template_name, self._context(template, version))

    def post(self, request, template_id=None, version_id=None):
        template, version = self._resolve_objects(template_id, version_id)
        if version and version.status != MediaTemplateVersion.Status.DRAFT:
            messages.error(request, 'Esta versão já foi publicada e não pode ser editada.')
            return redirect('admin_external_media_template_detail', template_id=version.template_id)

        form = AdminMediaTemplateVersionForm(request.POST, request.FILES, instance=version)
        candidate = version or MediaTemplateVersion(template=template, version=self._next_version(template))
        block_formset = AdminMediaTemplateBlockFormSet(
            request.POST, request.FILES, instance=candidate, prefix='blocks',
        )

        if form.is_valid() and block_formset.is_valid():
            with transaction.atomic():
                saved_version = form.save(commit=False)
                if not saved_version.pk:
                    saved_version.template = template
                    saved_version.version = candidate.version
                saved_version.status = MediaTemplateVersion.Status.DRAFT
                saved_version.save()
                block_formset.instance = saved_version
                block_formset.save()
                form.sync_advanced_plugins(saved_version)
            messages.success(request, f'Versão v{saved_version.version} salva como rascunho.')
            return redirect('admin_external_media_template_detail', template_id=saved_version.template_id)

        messages.error(request, 'Revise a versão e os blocos.')
        return render(request, self.template_name, self._context(template, version, form, block_formset))

    @staticmethod
    def _next_version(template):
        return (template.versions.aggregate(last=Max('version'))['last'] or 0) + 1

    @staticmethod
    def _resolve_objects(template_id, version_id):
        if version_id:
            version = get_object_or_404(MediaTemplateVersion.objects.select_related('template'), id=version_id)
            return version.template, version
        return get_object_or_404(MediaTemplate, id=template_id), None

    def _context(self, template, version, form=None, block_formset=None):
        if form is None:
            form = AdminMediaTemplateVersionForm(instance=version)
        instance = version or MediaTemplateVersion(template=template)
        if block_formset is None:
            block_formset = AdminMediaTemplateBlockFormSet(instance=instance, prefix='blocks')
        version_label = f'v{version.version}' if version else f'v{self._next_version(template)}'
        render_presets = RenderPreset.objects.order_by('name')
        subtitle_styles = SubtitleStyle.objects.order_by('name')
        background_musics = Music.objects.order_by('name', 'singer')
        return {
            'template': template,
            'version': version,
            'form': form,
            'block_formset': block_formset,
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
            'title': f'{template.name} {version_label}',
        }


class AdminExternalMediaVersionPublishView(LoginRequiredMixin, AdminRequiredMixin, View):
    def post(self, request, version_id):
        version = get_object_or_404(MediaTemplateVersion.objects.select_related('template'), id=version_id)
        if not version.blocks.exists():
            messages.error(request, 'Adicione pelo menos um bloco antes de publicar.')
            return redirect('admin_external_media_template_detail', template_id=version.template_id)

        version.status = MediaTemplateVersion.Status.PUBLISHED
        version.published_at = version.published_at or timezone.now()
        version.save(update_fields=['status', 'published_at', 'update_at'])
        messages.success(request, f'{version.template.name} v{version.version} publicada.')
        return redirect('admin_external_media_template_detail', template_id=version.template_id)


class AdminExternalMediaVersionDuplicateView(LoginRequiredMixin, AdminRequiredMixin, View):
    def post(self, request, version_id):
        source = get_object_or_404(
            MediaTemplateVersion.objects.prefetch_related('blocks', 'plugins').select_related('template'),
            id=version_id,
        )
        with transaction.atomic():
            clone = MediaTemplateVersion.objects.create(
                template=source.template,
                version=AdminExternalMediaVersionFormView._next_version(source.template),
                status=MediaTemplateVersion.Status.DRAFT,
                changelog=f'Criada a partir da versão {source.version}.',
                preset=source.preset,
                subtitle_style=source.subtitle_style,
                translated_subtitle_style=source.translated_subtitle_style,
                original_language=source.original_language,
                output_languages=list(source.output_languages),
                default_settings=dict(source.default_settings),
                allowed_overrides=list(source.allowed_overrides),
                intro_video=source.intro_video.name,
                outro_video=source.outro_video.name,
                lut_file=source.lut_file.name,
                background_music=source.background_music,
                music_file=source.music_file.name,
                music_volume=source.music_volume,
                fade_in_seconds=source.fade_in_seconds,
                fade_out_seconds=source.fade_out_seconds,
            )
            MediaTemplateBlock.objects.bulk_create([
                MediaTemplateBlock(
                    version=clone,
                    key=block.key,
                    name=block.name,
                    description=block.description,
                    order=block.order,
                    is_required=block.is_required,
                    allows_multiple=block.allows_multiple,
                    min_occurrences=block.min_occurrences,
                    max_occurrences=block.max_occurrences,
                    default_video=block.default_video.name,
                )
                for block in source.blocks.all()
            ])
            MediaTemplatePlugin.objects.bulk_create([
                MediaTemplatePlugin(
                    version=clone,
                    code=plugin.code,
                    order=plugin.order,
                    is_enabled=plugin.is_enabled,
                    user_can_override=plugin.user_can_override,
                    configuration=dict(plugin.configuration),
                )
                for plugin in source.plugins.all()
            ])

        messages.success(request, f'Nova versão v{clone.version} criada como rascunho.')
        return redirect('admin_external_media_version_edit', version_id=clone.id)


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
        try:
            style.delete()
            messages.success(request, f'Estilo "{name}" removido.')
        except ProtectedError:
            style.is_active = False
            style.save(update_fields=['is_active', 'update_at'])
            messages.warning(request, f'Estilo "{name}" está em uso e foi desativado.')
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
