from django.contrib import admin
from .models import ministry, evangelism, follow_up, member, visitor, neighborhood, user, event, testimony, prayer_request, music
from .models.schedule import ScaleDivision, DivisionMember
from .models.external_media import (
    ExternalMediaJob, ExternalMediaProject, ExternalMediaProjectExport, GlossaryTerm, MasteringProfile, MediaAsset,
    MediaTemplate, MediaTemplateBlock, MediaTemplatePlugin, MediaTemplateVersion,
    OverlayPreset, ProjectBlockMedia, ProjectOverlay, ProjectPipelineStep, ProxyProfile, RenderPreset, SubtitleCue, SubtitleStyle,
    SubtitleTrack, VideoMasteringJob,
)
from django.utils import timezone

admin.site.register(ministry.Ministry)
admin.site.register(follow_up.FollowUp)
admin.site.register(follow_up.FollowUpReport)
admin.site.register(member.Member)
admin.site.register(visitor.Visitor)
admin.site.register(neighborhood.Neighborhood)
admin.site.register(user.User)
admin.site.register(GlossaryTerm)
admin.site.register(RenderPreset)
admin.site.register(ProxyProfile)
admin.site.register(SubtitleStyle)


@admin.register(OverlayPreset)
class OverlayPresetAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'overlay_type', 'is_active')
    list_filter = ('overlay_type', 'is_active')
    search_fields = ('name', 'code')
    prepopulated_fields = {'code': ('name',)}


@admin.register(ProjectOverlay)
class ProjectOverlayAdmin(admin.ModelAdmin):
    list_display = ('overlay_id', 'project', 'overlay_type', 'source', 'is_enabled')
    list_filter = ('overlay_type', 'source', 'is_enabled')
    search_fields = ('overlay_id', 'project__name', 'purpose')


@admin.register(MasteringProfile)
class MasteringProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'target_lufs', 'true_peak_db', 'is_default', 'is_active')
    list_editable = ('is_default', 'is_active')
    prepopulated_fields = {'code': ('name',)}


@admin.register(VideoMasteringJob)
class VideoMasteringJobAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'mastering_profile', 'status', 'video_reencoded', 'created_at')
    list_filter = ('status', 'video_reencoded', 'mastering_profile')
    search_fields = ('name', 'created_by__name')
    readonly_fields = ('public_id', 'input_metrics', 'output_metrics', 'started_at', 'finished_at')


class MediaTemplateBlockInline(admin.StackedInline):
    model = MediaTemplateBlock
    extra = 0
    fields = (
        'key', 'name', 'description', 'order', 'is_required', 'allows_multiple',
        'min_occurrences', 'max_occurrences', 'skip_extra_processing', 'default_video',
        'overlay_definitions',
    )

    def has_add_permission(self, request, obj=None):
        return not obj or obj.status == MediaTemplateVersion.Status.DRAFT

    def has_change_permission(self, request, obj=None):
        return not obj or obj.status == MediaTemplateVersion.Status.DRAFT

    def has_delete_permission(self, request, obj=None):
        return not obj or obj.status == MediaTemplateVersion.Status.DRAFT


class MediaTemplatePluginInline(admin.TabularInline):
    model = MediaTemplatePlugin
    extra = 0
    fields = ('code', 'order', 'is_enabled', 'user_can_override', 'configuration')

    def has_add_permission(self, request, obj=None):
        return not obj or obj.status == MediaTemplateVersion.Status.DRAFT

    def has_change_permission(self, request, obj=None):
        return not obj or obj.status == MediaTemplateVersion.Status.DRAFT

    def has_delete_permission(self, request, obj=None):
        return not obj or obj.status == MediaTemplateVersion.Status.DRAFT


@admin.register(MediaTemplate)
class MediaTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'latest_version', 'is_active', 'update_at')
    list_filter = ('category', 'is_active')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}

    @admin.display(description='Versão atual')
    def latest_version(self, obj):
        version = obj.published_version
        return f'v{version.version}' if version else 'Sem publicação'


@admin.register(MediaTemplateVersion)
class MediaTemplateVersionAdmin(admin.ModelAdmin):
    list_display = ('template', 'version', 'status', 'preset', 'published_at')
    list_filter = ('status', 'template__category')
    search_fields = ('template__name', 'changelog')
    inlines = [MediaTemplateBlockInline, MediaTemplatePluginInline]
    actions = ['create_new_version']

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status != MediaTemplateVersion.Status.DRAFT:
            return [field.name for field in obj._meta.fields if field.name not in {'id'}]
        return ('published_at',)

    def save_model(self, request, obj, form, change):
        if obj.status == MediaTemplateVersion.Status.PUBLISHED and not obj.published_at:
            obj.published_at = timezone.now()
        super().save_model(request, obj, form, change)

    @admin.action(description='Criar nova versão em rascunho')
    def create_new_version(self, request, queryset):
        created = 0
        for source in queryset.prefetch_related('blocks', 'plugins'):
            next_version = (source.template.versions.order_by('-version').values_list('version', flat=True).first() or 0) + 1
            clone = MediaTemplateVersion.objects.create(
                template=source.template,
                version=next_version,
                status=MediaTemplateVersion.Status.DRAFT,
                changelog=f'Criada a partir da versão {source.version}.',
                preset=source.preset,
                subtitle_style=source.subtitle_style,
                translated_subtitle_style=source.translated_subtitle_style,
                original_language=source.original_language,
                output_languages=list(source.output_languages),
                interactive_preview_enabled=source.interactive_preview_enabled,
                preview_proxy_profile=source.preview_proxy_profile,
                preview_editable_capabilities=list(source.preview_editable_capabilities),
                preview_confidence_thresholds=dict(source.preview_confidence_thresholds),
                subtitles_enabled=source.subtitles_enabled,
                translated_subtitles_enabled=source.translated_subtitles_enabled,
                default_settings=dict(source.default_settings),
                allowed_overrides=list(source.allowed_overrides),
                intro_video=source.intro_video.name,
                outro_video=source.outro_video.name,
                lut_file=source.lut_file.name,
                color_lut=source.color_lut,
                lut_intensity=source.lut_intensity,
                background_music=source.background_music,
                music_file=source.music_file.name,
                music_volume=source.music_volume,
                fade_in_seconds=source.fade_in_seconds,
                fade_out_seconds=source.fade_out_seconds,
            )
            MediaTemplateBlock.objects.bulk_create([
                MediaTemplateBlock(
                    version=clone, key=item.key, name=item.name, description=item.description,
                    order=item.order, is_required=item.is_required, allows_multiple=item.allows_multiple,
                    min_occurrences=item.min_occurrences, max_occurrences=item.max_occurrences,
                    skip_extra_processing=item.skip_extra_processing,
                    remove_background_voice=item.remove_background_voice,
                    overlay_definitions=list(item.overlay_definitions or []),
                    default_video=item.default_video.name,
                ) for item in source.blocks.all()
            ])
            MediaTemplatePlugin.objects.bulk_create([
                MediaTemplatePlugin(
                    version=clone, code=item.code, order=item.order, is_enabled=item.is_enabled,
                    user_can_override=item.user_can_override, configuration=dict(item.configuration),
                ) for item in source.plugins.all()
            ])
            created += 1
        self.message_user(request, f'{created} nova(s) versão(ões) criada(s) como rascunho.')


class ProjectBlockMediaInline(admin.TabularInline):
    model = ProjectBlockMedia
    extra = 0
    readonly_fields = ('block', 'file', 'original_filename', 'position', 'duration_ms', 'file_size')
    can_delete = False


class ProjectPipelineStepInline(admin.TabularInline):
    model = ProjectPipelineStep
    extra = 0
    readonly_fields = ('code', 'label', 'order', 'status', 'progress', 'started_at', 'finished_at', 'message')
    can_delete = False


@admin.register(ExternalMediaProject)
class ExternalMediaProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'template_version', 'created_by', 'status', 'progress', 'created_at')
    list_filter = ('status', 'template_version__template')
    search_fields = ('name', 'created_by__name')
    readonly_fields = (
        'public_id', 'created_by', 'template_version', 'configuration', 'status', 'progress',
        'current_step', 'error_message', 'started_at', 'finished_at', 'celery_task_id', 'render_job',
    )
    inlines = [ProjectBlockMediaInline, ProjectPipelineStepInline]


@admin.register(ExternalMediaProjectExport)
class ExternalMediaProjectExportAdmin(admin.ModelAdmin):
    list_display = ('project', 'format', 'status', 'progress', 'created_by', 'created_at')
    list_filter = ('format', 'status')
    search_fields = ('project__name', 'created_by__name')
    readonly_fields = (
        'public_id', 'project', 'created_by', 'format', 'status', 'progress', 'current_step',
        'error_message', 'archive', 'timeline_json', 'compatibility', 'validation_report',
        'celery_task_id', 'download_count', 'started_at', 'finished_at',
    )


@admin.register(ExternalMediaJob)
class ExternalMediaJobAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'status', 'progress', 'preset', 'created_at')
    list_filter = ('status', 'preset')
    search_fields = ('name', 'created_by__name')


admin.site.register(MediaAsset)
admin.site.register(SubtitleCue)
admin.site.register(SubtitleTrack)


@admin.register(prayer_request.PrayerRequest)
class PrayerRequestAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name', 'content')
    readonly_fields = ('created_at',)


@admin.register(evangelism.Evangelized)
class EvangelizedAdmin(admin.ModelAdmin):
    list_display = ('name', 'gender', 'phone', 'neighborhood', 'evangelism_date', 'evangelized_by', 'conversion')
    list_filter = ('conversion', 'gender', 'evangelism_date', 'neighborhood')
    search_fields = ('name', 'phone', 'address')
    date_hierarchy = 'evangelism_date'
    readonly_fields = ('evangelism_date',)

@admin.register(event.Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'event_date', 'location', 'is_recurring', 'recurrence_pattern', 'is_visible')
    list_filter = ('is_recurring', 'recurrence_pattern', 'event_date')
    search_fields = ('title', 'description', 'location')
    prepopulated_fields = {'slug': ('title',)}
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('title', 'description', 'banner', 'slug')
        }),
        ('Data e Horário', {
            'fields': ('event_date', 'event_time', 'display_start', 'display_end')
        }),
        ('Localização e Links', {
            'fields': ('location', 'link_more_info', 'link_type')
        }),
        ('Recorrência', {
            'fields': ('is_recurring', 'recurrence_pattern', 'recurrence_description'),
            'classes': ('collapse',),
            'description': 'Configure aqui se o evento se repete regularmente'
        }),
    )


class ScaleDivisionInline(admin.TabularInline):
    model = ScaleDivision
    fk_name = 'parent'
    extra = 0
    fields = ('name', 'order', 'is_active')
    verbose_name = 'Subdivisão'
    verbose_name_plural = 'Subdivisões'


@admin.register(ScaleDivision)
class ScaleDivisionAdmin(admin.ModelAdmin):
    list_display = ('name', 'schedule', 'parent', 'order', 'is_active')
    list_filter = ('schedule', 'is_active')
    search_fields = ('name', 'schedule__title')
    list_editable = ('order', 'is_active')
    inlines = [ScaleDivisionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('schedule', 'parent')


class DivisionMemberInline(admin.TabularInline):
    model = DivisionMember
    extra = 0
    autocomplete_fields = ['member']


@admin.register(DivisionMember)
class DivisionMemberAdmin(admin.ModelAdmin):
    list_display = ('member', 'division', 'schedule_day')
    list_filter = ('division__schedule', 'division')
    search_fields = ('member__name', 'division__name')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'member', 'division', 'division__schedule', 'schedule_day'
        )


@admin.register(testimony.Testimony)
class TestimonyAdmin(admin.ModelAdmin):
    list_display = ('author_name', 'category', 'is_approved', 'show_on_home', 'created_at')
    list_filter = ('is_approved', 'show_on_home', 'category')
    search_fields = ('author_name', 'content')
    list_editable = ('is_approved', 'show_on_home')
    fieldsets = (
        ('Informações do Testemunho', {
            'fields': ('author_name', 'member', 'photo', 'content', 'category', 'testimony_date')
        }),
        ('Instagram', {
            'fields': ('instagram_url',),
            'description': 'Cole a URL de um post ou reel do Instagram para exibir o vídeo na página de testemunhos'
        }),
        ('Exibição', {
            'fields': ('is_approved', 'show_on_home')
        }),
    )


class ChordSheetInline(admin.TabularInline):
    model = music.ChordSheet
    extra = 1
    fields = ('file', 'tone', 'order')
    ordering = ('order',)


@admin.register(music.Music)
class MusicAdmin(admin.ModelAdmin):
    list_display = ('name', 'singer', 'tempo', 'chord_count')
    list_filter = ('tempo', 'created_at')
    search_fields = ('name', 'singer')
    inlines = [ChordSheetInline]
    fieldsets = (
        ('Informações da Música', {
            'fields': ('name', 'singer', 'tempo')
        }),
        ('Campo Legado', {
            'fields': ('chord_sheet',),
            'description': 'Este campo será descontinuado. Use a seção de Cifras abaixo.',
            'classes': ('collapse',)
        }),
    )
    
    def chord_count(self, obj):
        count = obj.chordsheets.count()
        return f"{count} cifra{'s' if count != 1 else ''}"
    chord_count.short_description = 'Cifras'
