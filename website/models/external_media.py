import uuid
from pathlib import Path

from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.files.storage import storages
from django.db import models

from ._base import BaseModel
from .member import Member


def external_media_upload_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    job_id = getattr(instance, 'public_id', None) or getattr(instance, 'job_id', 'pending')
    return f'external_media/{job_id}/source/original{suffix}'


def external_media_asset_path(instance, filename):
    return f'external_media/{instance.job.public_id}/outputs/{filename}'


def external_media_template_path(instance, filename):
    template_id = getattr(instance, 'template_id', None) or getattr(instance, 'pk', 'pending')
    return f'external_media/templates/{template_id}/{filename}'


def background_music_upload_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    track_id = getattr(instance, 'pk', None) or 'pending'
    return f'external_media/background_music/{track_id}{suffix}'


def video_mastering_upload_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    return f'external_media/mastering/{instance.public_id}/source/original{suffix}'


def video_mastering_output_path(instance, filename):
    suffix = Path(filename).suffix.lower() or '.mp4'
    return f'external_media/mastering/{instance.public_id}/output/mastered{suffix}'


def project_export_path(instance, filename):
    return f'external_media/projects/{instance.project.public_id}/exports/{instance.public_id}/{filename}'


def external_media_project_upload_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    block_ref = getattr(instance, 'block_id', None) or getattr(instance.block, 'pk', 'block')
    return (
        f'external_media/projects/{instance.project.public_id}/b{block_ref}/'
        f'{instance.position:03d}{suffix}'
    )


def external_media_project_preview_path(instance, filename):
    block_ref = getattr(instance, 'block_id', None) or getattr(instance.block, 'pk', 'block')
    return f'external_media/projects/{instance.project.public_id}/b{block_ref}/previews/{instance.position:03d}.mp4'


def default_output_languages():
    return ['pt', 'en']


def get_external_media_storage():
    return storages['external_media']


def format_processing_duration(seconds):
    if seconds is None:
        return ''
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f'{hours}h')
    if minutes:
        parts.append(f'{minutes}min')
    if secs or not parts:
        parts.append(f'{secs}s')
    return ' '.join(parts)


class SubtitleStyle(BaseModel):
    class FontWeight(models.IntegerChoices):
        REGULAR = 400, 'Regular'
        MEDIUM = 500, 'Medium'
        SEMIBOLD = 600, 'SemiBold'
        BOLD = 700, 'Bold'
        EXTRABOLD = 800, 'ExtraBold'
        BLACK = 900, 'Black'

    class Alignment(models.IntegerChoices):
        BOTTOM_LEFT = 1, 'Inferior esquerdo'
        BOTTOM_CENTER = 2, 'Inferior centro'
        BOTTOM_RIGHT = 3, 'Inferior direito'
        MIDDLE_LEFT = 4, 'Meio esquerdo'
        MIDDLE_CENTER = 5, 'Meio centro'
        MIDDLE_RIGHT = 6, 'Meio direito'
        TOP_LEFT = 7, 'Superior esquerdo'
        TOP_CENTER = 8, 'Superior centro'
        TOP_RIGHT = 9, 'Superior direito'

    name = models.CharField(max_length=100, unique=True)
    font_name = models.CharField(max_length=100, default='Arial')
    font_weight = models.PositiveSmallIntegerField(
        choices=FontWeight.choices,
        default=FontWeight.BOLD,
    )
    font_size = models.PositiveIntegerField(default=48)
    primary_color = models.CharField(max_length=10, default='#FFFFFF')
    primary_opacity = models.PositiveSmallIntegerField(default=100)
    background_enabled = models.BooleanField(default=False)
    background_color = models.CharField(max_length=10, default='#000000')
    background_opacity = models.PositiveSmallIntegerField(default=70)
    background_padding_x = models.PositiveSmallIntegerField(default=14)
    background_padding_y = models.PositiveSmallIntegerField(default=8)
    background_height_percent = models.PositiveSmallIntegerField(default=100)
    background_radius = models.PositiveSmallIntegerField(default=10)
    outline_color = models.CharField(max_length=10, default='#000000')
    outline_width = models.PositiveIntegerField(default=3)
    shadow = models.PositiveIntegerField(default=1)
    shadow_angle = models.PositiveSmallIntegerField(default=45)
    shadow_size = models.PositiveSmallIntegerField(default=0)
    shadow_blur = models.PositiveSmallIntegerField(default=0)
    shadow_opacity = models.PositiveSmallIntegerField(default=70)
    margin_bottom = models.PositiveIntegerField(default=60)
    alignment = models.PositiveSmallIntegerField(
        choices=Alignment.choices,
        default=Alignment.BOTTOM_CENTER,
    )
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
    processing_project = models.ForeignKey(
        'ExternalMediaProject',
        on_delete=models.CASCADE,
        related_name='processing_history',
        blank=True,
        null=True,
        help_text='Projeto que originou este processamento.',
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
            models.Index(fields=['processing_project', '-created_at'], name='ext_job_project_created_idx'),
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
    def duration_label(self):
        return format_processing_duration(self.duration_seconds)

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
    """Stable template identity. The admin edits the latest version as the current template."""

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

    @property
    def current_version(self):
        return self.versions.order_by('-version').first()


class BackgroundMusicTrack(BaseModel):
    class Category(models.TextChoices):
        INSTRUMENTAL = 'instrumental', 'Instrumental'
        WORSHIP = 'worship', 'Adoração'
        CALM = 'calm', 'Calma'
        UPBEAT = 'upbeat', 'Animada'
        CINEMATIC = 'cinematic', 'Cinematográfica'
        ANNOUNCEMENT = 'announcement', 'Anúncio'
        OTHER = 'other', 'Outra'

    TEMPO_CHOICES = [
        ('rapida', 'Rápida'),
        ('media', 'Média'),
        ('lenta', 'Lenta'),
    ]

    name = models.CharField(max_length=200)
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        default=Category.INSTRUMENTAL,
        verbose_name='Categoria',
    )
    tempo = models.CharField(
        max_length=10,
        choices=TEMPO_CHOICES,
        blank=True,
        default='',
        verbose_name='Andamento',
    )
    audio_file = models.FileField(
        upload_to=background_music_upload_path,
        storage=get_external_media_storage,
        max_length=255,
        blank=True,
        help_text='Arquivo de áudio usado como trilha de fundo nos templates.',
    )

    class Meta:
        ordering = ['category', 'name']
        verbose_name = 'Trilha de fundo'
        verbose_name_plural = 'Trilhas de fundo'

    def __str__(self):
        return self.name


class MasteringProfile(BaseModel):
    """Loudness/true-peak target used by AudioMasteringService for the final stereo mix.

    Kept independent from mixing settings on purpose: mastering only ever sees the
    finished mix, never individual dialogue/music elements (see AudioMasteringService).
    """

    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=50, unique=True)
    target_lufs = models.DecimalField(max_digits=4, decimal_places=1, default=-16.0, verbose_name='Loudness alvo (LUFS)')
    true_peak_db = models.DecimalField(max_digits=4, decimal_places=1, default=-1.0, verbose_name='True Peak máximo (dBTP)')
    bus_compression_enabled = models.BooleanField(default=False, verbose_name='Compressão de bus leve')
    limiter_enabled = models.BooleanField(default=True, verbose_name='Limiter de true peak')
    eq_profile = models.JSONField(default=dict, blank=True, verbose_name='Perfil de EQ')
    dynamic_range_target = models.DecimalField(
        max_digits=4, decimal_places=1, default=11.0, verbose_name='Faixa dinâmica alvo (LU)',
    )
    compression_limits = models.JSONField(default=dict, blank=True, verbose_name='Limites de compressão')
    limiter_settings = models.JSONField(default=dict, blank=True, verbose_name='Configuração do limiter')
    low_frequency_control = models.DecimalField(
        max_digits=4, decimal_places=1, default=0, verbose_name='Controle de graves (dB)',
    )
    high_frequency_control = models.DecimalField(
        max_digits=4, decimal_places=1, default=0, verbose_name='Controle de agudos (dB)',
    )
    max_gain_db = models.DecimalField(
        max_digits=4, decimal_places=1, default=12.0, verbose_name='Ganho máximo (dB)',
    )
    max_limiter_reduction_db = models.DecimalField(
        max_digits=4, decimal_places=1, default=4.0, verbose_name='Redução máxima do limiter (dB)',
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, verbose_name='Perfil padrão')

    class Meta:
        ordering = ['name']
        verbose_name = 'Perfil de masterização'
        verbose_name_plural = 'Perfis de masterização'

    def __str__(self):
        return self.name


class VideoMasteringJob(BaseModel):
    """Masterização independente de um vídeo finalizado.

    O original é imutável e sempre é a origem de qualquer reprocessamento. Este fluxo
    deliberadamente não conhece diálogo, música ou templates de edição.
    """

    class Status(models.TextChoices):
        UPLOADING = 'UPLOADING', 'Enviando vídeo'
        ANALYZING = 'ANALYZING', 'Analisando áudio'
        READY = 'READY', 'Pronto para masterizar'
        MASTERING = 'MASTERING', 'Masterizando áudio'
        VALIDATING = 'VALIDATING', 'Validando resultado'
        MUXING = 'MUXING', 'Finalizando vídeo'
        FINISHED = 'FINISHED', 'Finalizado'
        ERROR = 'ERROR', 'Erro'

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=180)
    created_by = models.ForeignKey(
        Member, on_delete=models.PROTECT, related_name='video_mastering_jobs',
    )
    original_video = models.FileField(
        upload_to=video_mastering_upload_path, storage=get_external_media_storage, max_length=255,
    )
    output_video = models.FileField(
        upload_to=video_mastering_output_path,
        storage=get_external_media_storage,
        max_length=255,
        blank=True,
    )
    mastering_profile = models.ForeignKey(
        MasteringProfile,
        on_delete=models.PROTECT,
        related_name='video_jobs',
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADING)
    progress = models.PositiveSmallIntegerField(default=0)
    current_step = models.CharField(max_length=180, blank=True)
    error_message = models.TextField(blank=True)
    input_metrics = models.JSONField(default=dict, blank=True)
    output_metrics = models.JSONField(default=dict, blank=True)
    input_lufs = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    output_lufs = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    input_true_peak = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    output_true_peak = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    gain_applied = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    limiter_gain_reduction = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    video_reencoded = models.BooleanField(default=False)
    download_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    celery_task_id = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_by', '-created_at'], name='website_vid_created_ba1fd6_idx'),
            models.Index(fields=['status'], name='website_vid_status_c4fdda_idx'),
        ]
        verbose_name = 'Masterização de vídeo'
        verbose_name_plural = 'Masterizações de vídeo'

    def __str__(self):
        return self.name

    @property
    def duration_seconds(self):
        if not self.started_at:
            return None
        end = self.finished_at or self.update_at
        return max(0, int((end - self.started_at).total_seconds()))

    @property
    def duration_label(self):
        return format_processing_duration(self.duration_seconds)


class SpeechFillerTerm(BaseModel):
    """Reusable vocabulary used by the speech-filler removal pipeline."""

    language = models.CharField(
        max_length=10,
        choices=ExternalMediaJob.LANGUAGE_CHOICES,
        default='pt',
    )
    text = models.CharField(max_length=80, verbose_name='Termo')
    is_active = models.BooleanField(default=True, verbose_name='Disponível para templates')

    class Meta:
        ordering = ['language', 'text']
        verbose_name = 'Vocabulário de vício de fala'
        verbose_name_plural = 'Vocabulários de vícios de fala'

    def __str__(self):
        return self.text


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
    filler_terms = models.ManyToManyField(
        'SpeechFillerTerm',
        related_name='template_versions',
        blank=True,
        verbose_name='Vícios de fala ativos',
    )
    intro_video = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )
    outro_video = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )
    lut_file = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )
    lut_intensity = models.PositiveSmallIntegerField(
        default=50,
        validators=[MaxValueValidator(100)],
        verbose_name='Intensidade do LUT (%)',
        help_text='Mistura o LUT com a imagem original. 50% é o padrão recomendado.',
    )
    background_music = models.ForeignKey(
        BackgroundMusicTrack,
        on_delete=models.PROTECT,
        related_name='template_versions',
        blank=True,
        null=True,
    )
    music_file = models.FileField(
        upload_to=external_media_template_path, storage=get_external_media_storage, blank=True,
    )
    music_volume = models.DecimalField(max_digits=4, decimal_places=2, default=0.15)
    fade_in_seconds = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fade_out_seconds = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    dialogue_processing_enabled = models.BooleanField(default=False, verbose_name='Tratamento de diálogo')
    dialogue_processing_config = models.JSONField(
        default=dict, blank=True, verbose_name='Configuração avançada de tratamento de diálogo',
    )
    audio_mixing_enabled = models.BooleanField(default=True, verbose_name='Mixagem inteligente')
    audio_ducking_enabled = models.BooleanField(default=True, verbose_name='Ducking automático')
    audio_spectral_ducking_enabled = models.BooleanField(default=False, verbose_name='Ducking espectral')
    audio_mixing_config = models.JSONField(default=dict, blank=True, verbose_name='Configuração avançada de mixagem')
    audio_mastering_enabled = models.BooleanField(default=False, verbose_name='Masterização')
    mastering_profile = models.ForeignKey(
        MasteringProfile,
        on_delete=models.PROTECT,
        related_name='template_versions',
        blank=True,
        null=True,
        verbose_name='Perfil de masterização',
    )
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['template__name', '-version']
        constraints = [
            models.UniqueConstraint(fields=['template', 'version'], name='unique_media_template_version'),
        ]
        verbose_name = 'Versão de template'
        verbose_name_plural = 'Versões de templates'

    def __str__(self):
        return self.template.name


class MediaTemplateBlock(BaseModel):
    version = models.ForeignKey(MediaTemplateVersion, on_delete=models.CASCADE, related_name='blocks')
    key = models.SlugField(max_length=80)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_required = models.BooleanField(default=True)
    allows_multiple = models.BooleanField(default=True)
    min_occurrences = models.PositiveSmallIntegerField(default=1)
    # Zero representa quantidade ilimitada. Mantemos o campo apenas para
    # compatibilidade com templates antigos já cadastrados.
    max_occurrences = models.PositiveSmallIntegerField(default=0)
    skip_extra_processing = models.BooleanField(default=False)
    remove_background_voice = models.BooleanField(
        default=False,
        verbose_name='Remover voz de fundo',
        help_text='Remove falas isoladas de um entrevistador/voz sem microfone. Sobreposições são preservadas.',
    )
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

    @property
    def duration_label(self):
        return format_processing_duration(self.duration_seconds)

    @property
    def duration_minutes(self):
        """Processing duration rounded up to a whole minute for compact lists."""
        seconds = self.duration_seconds
        if seconds is None:
            return None
        return (seconds + 59) // 60


class ProjectCustomBlock(BaseModel):
    project = models.ForeignKey(ExternalMediaProject, on_delete=models.CASCADE, related_name='custom_blocks')
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    position = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ['position', 'pk']
        constraints = [models.UniqueConstraint(fields=['project', 'position'], name='unique_project_custom_block_position')]

    @property
    def is_required(self):
        return False

    @property
    def max_occurrences(self):
        return 0

    @property
    def default_video(self):
        return None


class ProjectBlockMedia(BaseModel):
    class PreviewStatus(models.TextChoices):
        PENDING = 'PENDING', 'Preparando preview'
        READY = 'READY', 'Preview pronto'
        ERROR = 'ERROR', 'Falha no preview'

    project = models.ForeignKey(ExternalMediaProject, on_delete=models.CASCADE, related_name='block_media')
    block = models.ForeignKey(MediaTemplateBlock, on_delete=models.PROTECT, related_name='project_media', null=True, blank=True)
    custom_block = models.ForeignKey(ProjectCustomBlock, on_delete=models.CASCADE, related_name='media', null=True, blank=True)
    file = models.FileField(
        upload_to=external_media_project_upload_path, storage=get_external_media_storage, max_length=255,
    )
    original_filename = models.CharField(max_length=255)
    position = models.PositiveSmallIntegerField(default=1)
    duration_ms = models.PositiveBigIntegerField(blank=True, null=True)
    trim_start_ms = models.PositiveBigIntegerField(default=0)
    trim_end_ms = models.PositiveBigIntegerField(blank=True, null=True)
    file_size = models.PositiveBigIntegerField(default=0)
    thumbnail = models.ImageField(
        upload_to=external_media_project_upload_path,
        storage=get_external_media_storage,
        blank=True,
        max_length=255,
    )
    preview_file = models.FileField(upload_to=external_media_project_preview_path, storage=get_external_media_storage, blank=True, max_length=255)
    preview_status = models.CharField(max_length=16, choices=PreviewStatus.choices, default=PreviewStatus.PENDING)
    preview_error = models.CharField(max_length=255, blank=True)

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
        return f'{self.project}: {(self.block or self.custom_block).name} #{self.position}'


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


class ExternalMediaProjectExport(BaseModel):
    class Format(models.TextChoices):
        PREMIERE = 'PREMIERE', 'Adobe Premiere Pro'

    class Status(models.TextChoices):
        PREPARING = 'PREPARING', 'Preparando'
        BUILDING_TIMELINE = 'BUILDING_TIMELINE', 'Construindo timeline'
        CONVERTING = 'CONVERTING', 'Convertendo projeto'
        PACKAGING_ASSETS = 'PACKAGING_ASSETS', 'Organizando arquivos'
        VALIDATING = 'VALIDATING', 'Validando exportação'
        COMPRESSING = 'COMPRESSING', 'Compactando pacote'
        FINISHED = 'FINISHED', 'Finalizado'
        ERROR = 'ERROR', 'Erro'

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(
        ExternalMediaProject, on_delete=models.CASCADE, related_name='exports',
    )
    created_by = models.ForeignKey(
        Member, on_delete=models.PROTECT, related_name='external_media_exports',
    )
    format = models.CharField(max_length=16, choices=Format.choices, default=Format.PREMIERE)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PREPARING)
    progress = models.PositiveSmallIntegerField(default=0)
    current_step = models.CharField(max_length=180, blank=True)
    error_message = models.TextField(blank=True)
    archive = models.FileField(
        upload_to=project_export_path, storage=get_external_media_storage, max_length=255, blank=True,
    )
    timeline_json = models.FileField(
        upload_to=project_export_path, storage=get_external_media_storage, max_length=255, blank=True,
    )
    compatibility = models.JSONField(default=dict, blank=True)
    validation_report = models.JSONField(default=dict, blank=True)
    celery_task_id = models.CharField(max_length=255, blank=True)
    download_count = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', '-created_at'], name='website_ext_project_a76efe_idx'),
            models.Index(fields=['status'], name='website_ext_status_985ac0_idx'),
        ]
        verbose_name = 'Exportação de projeto de mídia'
        verbose_name_plural = 'Exportações de projetos de mídia'

    def __str__(self):
        return f'{self.project.name} · {self.get_format_display()}'

    @property
    def duration_seconds(self):
        if not self.started_at:
            return None
        end = self.finished_at or self.update_at
        return max(0, int((end - self.started_at).total_seconds()))

    @property
    def duration_label(self):
        return format_processing_duration(self.duration_seconds)
