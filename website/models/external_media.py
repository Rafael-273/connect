import uuid
from pathlib import Path

from django.core.validators import MinValueValidator
from django.core.files.storage import storages
from django.db import models

from ._base import BaseModel
from .member import Member
from .music import Music


def external_media_upload_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    job_id = getattr(instance, 'public_id', None) or getattr(instance, 'job_id', 'pending')
    return f'external_media/{job_id}/source/original{suffix}'


def external_media_asset_path(instance, filename):
    return f'external_media/{instance.job.public_id}/outputs/{filename}'


def external_media_template_path(instance, filename):
    template_id = getattr(instance, 'template_id', None) or getattr(instance, 'pk', 'pending')
    return f'external_media/templates/{template_id}/{filename}'


def external_media_project_upload_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    block_ref = getattr(instance, 'block_id', None) or getattr(instance.block, 'pk', 'block')
    return (
        f'external_media/projects/{instance.project.public_id}/b{block_ref}/'
        f'{instance.position:03d}{suffix}'
    )


def default_output_languages():
    return ['pt', 'en']


def get_external_media_storage():
    return storages['external_media']


class SubtitleStyle(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    font_name = models.CharField(max_length=100, default='Arial')
    font_size = models.PositiveIntegerField(default=48)
    primary_color = models.CharField(max_length=10, default='#FFFFFF')
    outline_color = models.CharField(max_length=10, default='#000000')
    outline_width = models.PositiveIntegerField(default=3)
    shadow = models.PositiveIntegerField(default=1)
    margin_bottom = models.PositiveIntegerField(default=60)
    alignment = models.PositiveSmallIntegerField(default=2)
    max_lines = models.PositiveSmallIntegerField(default=2)
    max_characters = models.PositiveSmallIntegerField(default=42)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Estilo de legenda'
        verbose_name_plural = 'Estilos de legenda'

    def __str__(self):
        return self.name


class RenderPreset(BaseModel):
    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=50, unique=True)
    width = models.PositiveIntegerField(blank=True, null=True)
    height = models.PositiveIntegerField(blank=True, null=True)
    video_codec = models.CharField(max_length=30, default='libx264')
    audio_codec = models.CharField(max_length=30, default='copy')
    video_crf = models.PositiveSmallIntegerField(default=23)
    extra_ffmpeg_args = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Preset de renderização'
        verbose_name_plural = 'Presets de renderização'

    def __str__(self):
        return self.name


class ExternalMediaJob(BaseModel):
    class Status(models.TextChoices):
        UPLOADING = 'UPLOADING', 'Enviando vídeo'
        PENDING = 'PENDING', 'Na fila'
        EXTRACTING_AUDIO = 'EXTRACTING_AUDIO', 'Extraindo áudio'
        TRANSCRIBING = 'TRANSCRIBING', 'Transcrevendo'
        GENERATING_SUBTITLES = 'GENERATING_SUBTITLES', 'Gerando legendas'
        TRANSLATING = 'TRANSLATING', 'Traduzindo'
        AWAITING_REVIEW = 'AWAITING_REVIEW', 'Aguardando revisão'
        RENDERING = 'RENDERING', 'Renderizando vídeos'
        FINISHED = 'FINISHED', 'Finalizado'
        ERROR = 'ERROR', 'Erro'
        CANCELLED = 'CANCELLED', 'Cancelado'

    LANGUAGE_CHOICES = [
        ('pt', 'Português'),
        ('en', 'Inglês'),
        ('es', 'Espanhol'),
        ('fr', 'Francês'),
        ('it', 'Italiano'),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=180)
    created_by = models.ForeignKey(
        Member,
        on_delete=models.PROTECT,
        related_name='external_media_jobs',
    )
    original_video = models.FileField(
        upload_to=external_media_upload_path,
        storage=get_external_media_storage,
    )
    original_language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, default='pt')
    output_languages = models.JSONField(default=default_output_languages)
    translation_model = models.CharField(max_length=100, default='gpt-4.1-mini')
    preset = models.ForeignKey(RenderPreset, on_delete=models.PROTECT, related_name='jobs')
    subtitle_style = models.ForeignKey(SubtitleStyle, on_delete=models.PROTECT, related_name='jobs')
    translated_subtitle_style = models.ForeignKey(
        SubtitleStyle,
        on_delete=models.PROTECT,
        related_name='translated_jobs',
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.UPLOADING)
    progress = models.PositiveSmallIntegerField(default=0)
    current_step = models.CharField(max_length=180, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    celery_task_id = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_by', '-created_at']),
            models.Index(fields=['status']),
        ]
        verbose_name = 'Processamento de mídia externa'
        verbose_name_plural = 'Processamentos de mídia externa'

    def __str__(self):
        return self.name

    @property
    def duration_seconds(self):
        if not self.started_at:
            return None
        end = self.finished_at or self.update_at
        return max(0, int((end - self.started_at).total_seconds()))

    @property
    def download_count(self):
        return sum(asset.download_count for asset in self.assets.all())


class SubtitleTrack(BaseModel):
    job = models.ForeignKey(ExternalMediaJob, on_delete=models.CASCADE, related_name='subtitle_tracks')
    language = models.CharField(max_length=10, choices=ExternalMediaJob.LANGUAGE_CHOICES)
    is_source = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['job', 'language'], name='unique_job_subtitle_language'),
        ]
        ordering = ['language']

    def __str__(self):
        return f'{self.job.name} ({self.language})'


class SubtitleCue(BaseModel):
    track = models.ForeignKey(SubtitleTrack, on_delete=models.CASCADE, related_name='cues')
    cue_index = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    start_ms = models.PositiveBigIntegerField()
    end_ms = models.PositiveBigIntegerField()
    text = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['track', 'cue_index'], name='unique_track_cue_index'),
            models.CheckConstraint(check=models.Q(end_ms__gt=models.F('start_ms')), name='cue_end_after_start'),
        ]
        ordering = ['cue_index']

    def __str__(self):
        return f'{self.track} #{self.cue_index}'


class GlossaryTerm(BaseModel):
    source_language = models.CharField(max_length=10, choices=ExternalMediaJob.LANGUAGE_CHOICES, default='pt')
    target_language = models.CharField(max_length=10, choices=ExternalMediaJob.LANGUAGE_CHOICES, default='en')
    source_text = models.CharField(max_length=255)
    translated_text = models.CharField(max_length=255)
    notes = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['source_language', 'target_language', 'source_text'],
                name='unique_external_media_glossary_term',
            ),
        ]
        ordering = ['source_text']
        verbose_name = 'Termo do glossário'
        verbose_name_plural = 'Glossário de mídia externa'

    def __str__(self):
        return f'{self.source_text} → {self.translated_text}'


class MediaAsset(BaseModel):
    class Kind(models.TextChoices):
        VIDEO = 'VIDEO', 'Vídeo renderizado'
        SRT = 'SRT', 'Legenda SRT'
        VTT = 'VTT', 'Legenda VTT'

    job = models.ForeignKey(ExternalMediaJob, on_delete=models.CASCADE, related_name='assets')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    language = models.CharField(max_length=10, choices=ExternalMediaJob.LANGUAGE_CHOICES)
    file = models.FileField(
        upload_to=external_media_asset_path,
        storage=get_external_media_storage,
    )
    file_size = models.PositiveBigIntegerField(default=0)
    download_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['job', 'kind', 'language'], name='unique_job_asset_kind_language'),
        ]
        ordering = ['language', 'kind']

    def __str__(self):
        return f'{self.job.name} - {self.kind} ({self.language})'


class MediaTemplate(BaseModel):
    """Stable template identity. Editable content lives in immutable versions."""

    class Category(models.TextChoices):
        ANNOUNCEMENT = 'ANNOUNCEMENT', 'Anúncios'
        STORIES = 'STORIES', 'Stories'
        TRANSLATION = 'TRANSLATION', 'Tradução'
        REELS = 'REELS', 'Reels'
        TESTIMONY = 'TESTIMONY', 'Testemunhos'
        OTHER = 'OTHER', 'Outros'

    name = models.CharField(max_length=140, unique=True)
    slug = models.SlugField(max_length=160, unique=True)
    category = models.CharField(max_length=24, choices=Category.choices, default=Category.OTHER)
    description = models.TextField(blank=True)
    thumbnail = models.ImageField(
        upload_to=external_media_template_path,
        storage=get_external_media_storage,
        blank=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Template de mídia'
        verbose_name_plural = 'Templates de mídia'

    def __str__(self):
        return self.name

    @property
    def published_version(self):
        return self.versions.filter(status=MediaTemplateVersion.Status.PUBLISHED).order_by('-version').first()


class MediaTemplateVersion(BaseModel):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Rascunho'
        PUBLISHED = 'PUBLISHED', 'Publicado'
        ARCHIVED = 'ARCHIVED', 'Arquivado'

    template = models.ForeignKey(MediaTemplate, on_delete=models.CASCADE, related_name='versions')
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    changelog = models.TextField(blank=True)
    preset = models.ForeignKey(RenderPreset, on_delete=models.PROTECT, related_name='template_versions')
    subtitle_style = models.ForeignKey(
        SubtitleStyle, on_delete=models.PROTECT, related_name='template_versions', blank=True, null=True,
    )
    translated_subtitle_style = models.ForeignKey(
        SubtitleStyle,
        on_delete=models.PROTECT,
        related_name='translated_template_versions',
        blank=True,
        null=True,
    )
    original_language = models.CharField(
        max_length=10, choices=ExternalMediaJob.LANGUAGE_CHOICES, default='pt',
    )
    output_languages = models.JSONField(default=default_output_languages)
    default_settings = models.JSONField(default=dict, blank=True)
    allowed_overrides = models.JSONField(default=list, blank=True)
    intro_video = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )
    outro_video = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )
    lut_file = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )
    background_music = models.ForeignKey(
        Music,
        on_delete=models.PROTECT,
        related_name='external_media_versions',
        blank=True,
        null=True,
    )
    music_file = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )
    music_volume = models.DecimalField(max_digits=4, decimal_places=2, default=0.15)
    fade_in_seconds = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fade_out_seconds = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['template__name', '-version']
        constraints = [
            models.UniqueConstraint(fields=['template', 'version'], name='unique_media_template_version'),
        ]
        verbose_name = 'Versão de template'
        verbose_name_plural = 'Versões de templates'

    def __str__(self):
        return f'{self.template.name} v{self.version}'


class MediaTemplateBlock(BaseModel):
    version = models.ForeignKey(MediaTemplateVersion, on_delete=models.CASCADE, related_name='blocks')
    key = models.SlugField(max_length=80)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_required = models.BooleanField(default=True)
    allows_multiple = models.BooleanField(default=False)
    min_occurrences = models.PositiveSmallIntegerField(default=1)
    max_occurrences = models.PositiveSmallIntegerField(default=1)
    default_video = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )

    class Meta:
        ordering = ['order', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['version', 'key'], name='unique_media_template_block_key'),
        ]
        verbose_name = 'Bloco do template'
        verbose_name_plural = 'Blocos do template'

    def __str__(self):
        return f'{self.version}: {self.name}'


class MediaTemplatePlugin(BaseModel):
    class Code(models.TextChoices):
        SILENCE_REMOVAL = 'silence_removal', 'Corte de silêncio'
        FILLER_REMOVAL = 'filler_removal', 'Remover vícios de fala'
        AUTO_TRACKING = 'auto_tracking', 'Auto Reframe inteligente'
        SUBTITLE_PT = 'subtitle_pt', 'Legenda PT'
        TRANSLATION_EN = 'translation_en', 'Tradução EN'
        LUT = 'lut', 'Aplicar LUT'
        INTRO = 'intro', 'Intro'
        OUTRO = 'outro', 'Tela final'
        MUSIC = 'music', 'Música'

    version = models.ForeignKey(MediaTemplateVersion, on_delete=models.CASCADE, related_name='plugins')
    code = models.CharField(max_length=32, choices=Code.choices)
    order = models.PositiveSmallIntegerField(default=0)
    is_enabled = models.BooleanField(default=True)
    user_can_override = models.BooleanField(default=False)
    configuration = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['order', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['version', 'code'], name='unique_media_template_plugin'),
        ]
        verbose_name = 'Plugin do template'
        verbose_name_plural = 'Plugins do template'

    def __str__(self):
        return f'{self.version}: {self.get_code_display()}'


class ExternalMediaProject(BaseModel):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Em preparação'
        PENDING = 'PENDING', 'Na fila'
        ASSEMBLING = 'ASSEMBLING', 'Montando vídeo'
        PROCESSING = 'PROCESSING', 'Processando'
        AWAITING_REVIEW = 'AWAITING_REVIEW', 'Aguardando revisão'
        FINISHED = 'FINISHED', 'Finalizado'
        ERROR = 'ERROR', 'Erro'
        CANCELLED = 'CANCELLED', 'Cancelado'

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=180)
    template_version = models.ForeignKey(
        MediaTemplateVersion, on_delete=models.PROTECT, related_name='projects',
    )
    created_by = models.ForeignKey(Member, on_delete=models.PROTECT, related_name='external_media_projects')
    configuration = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    progress = models.PositiveSmallIntegerField(default=0)
    current_step = models.CharField(max_length=180, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    celery_task_id = models.CharField(max_length=255, blank=True)
    render_job = models.OneToOneField(
        ExternalMediaJob, on_delete=models.SET_NULL, related_name='project', blank=True, null=True,
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_by', '-created_at']),
            models.Index(fields=['status']),
        ]
        verbose_name = 'Projeto de mídia'
        verbose_name_plural = 'Projetos de mídia'

    def __str__(self):
        return self.name

    @property
    def duration_seconds(self):
        if not self.started_at:
            return None
        end = self.finished_at or self.update_at
        return max(0, int((end - self.started_at).total_seconds()))


class ProjectBlockMedia(BaseModel):
    project = models.ForeignKey(ExternalMediaProject, on_delete=models.CASCADE, related_name='block_media')
    block = models.ForeignKey(MediaTemplateBlock, on_delete=models.PROTECT, related_name='project_media')
    file = models.FileField(
        upload_to=external_media_project_upload_path, storage=get_external_media_storage, max_length=255,
    )
    original_filename = models.CharField(max_length=255)
    position = models.PositiveSmallIntegerField(default=1)
    duration_ms = models.PositiveBigIntegerField(blank=True, null=True)
    file_size = models.PositiveBigIntegerField(default=0)
    thumbnail = models.ImageField(
        upload_to=external_media_project_upload_path,
        storage=get_external_media_storage,
        blank=True,
        max_length=255,
    )

    class Meta:
        ordering = ['block__order', 'position', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'block', 'position'], name='unique_project_block_media_position',
            ),
        ]
        verbose_name = 'Vídeo de bloco'
        verbose_name_plural = 'Vídeos dos blocos'

    def __str__(self):
        return f'{self.project}: {self.block.name} #{self.position}'


class ProjectPipelineStep(BaseModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pendente'
        RUNNING = 'RUNNING', 'Executando'
        FINISHED = 'FINISHED', 'Concluído'
        SKIPPED = 'SKIPPED', 'Ignorado'
        ERROR = 'ERROR', 'Erro'

    project = models.ForeignKey(ExternalMediaProject, on_delete=models.CASCADE, related_name='pipeline_steps')
    code = models.CharField(max_length=48)
    label = models.CharField(max_length=120)
    order = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    progress = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    message = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['order', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['project', 'code'], name='unique_project_pipeline_step'),
        ]
        verbose_name = 'Etapa do pipeline'
        verbose_name_plural = 'Etapas do pipeline'

    def __str__(self):
        return f'{self.project}: {self.label}'
