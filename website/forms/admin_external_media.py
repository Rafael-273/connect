import json
from decimal import Decimal
from math import gcd

from django import forms
from django.forms.models import BaseInlineFormSet
from django.forms import inlineformset_factory
from django.utils.text import slugify

from ..models.external_media import (
    BackgroundMusicTrack,
    ColorLUT,
    MasteringProfile,
    MediaTemplate,
    MediaTemplateBlock,
    MediaTemplatePlugin,
    MediaTemplateVersion,
    OverlayPreset,
    ProxyProfile,
    RenderPreset,
    SpeechFillerTerm,
    SubtitleStyle,
)


FIELD_CLASS = 'w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:border-[var(--color-primary)] bg-white text-sm'
CHECKBOX_CLASS = 'h-4 w-4 rounded border-gray-300 text-[var(--color-primary)] focus:ring-[var(--color-primary)]'
COLOR_CLASS = 'h-11 w-full rounded-lg border border-gray-300 bg-white p-1'
ADVANCED_PLUGIN_CODES = {
    MediaTemplatePlugin.Code.SILENCE_REMOVAL,
    MediaTemplatePlugin.Code.FILLER_REMOVAL,
    MediaTemplatePlugin.Code.AUTO_TRACKING,
}
ADVANCED_PLUGIN_CHOICES = [
    (MediaTemplatePlugin.Code.SILENCE_REMOVAL, 'Corte de silêncio'),
    (MediaTemplatePlugin.Code.FILLER_REMOVAL, 'Remover vícios de fala'),
    (MediaTemplatePlugin.Code.AUTO_TRACKING, 'Auto Reframe inteligente'),
]
RENDER_PRESET_VIDEO_CODEC_CHOICES = [
    ('libx264', 'H.264 (libx264) — compatível com telão, YouTube e redes sociais'),
    ('libx265', 'H.265 (libx265) — arquivo menor, menos compatível'),
]
RENDER_PRESET_AUDIO_CODEC_CHOICES = [
    ('copy', 'Copiar áudio original (recomendado)'),
    ('aac', 'Reencodar em AAC (MP4/web)'),
]
RENDER_PRESET_CRF_CHOICES = [
    (18, '18 — alta qualidade (arquivo maior)'),
    (20, '20 — muito boa qualidade'),
    (21, '21 — boa qualidade'),
    (23, '23 — equilibrado (padrão)'),
    (26, '26 — arquivo menor'),
    (28, '28 — menor qualidade'),
]
RENDER_PRESET_EXTRA_FFMPEG_CHOICES = [
    ('[]', 'Nenhum'),
    ('["-maxrate", "8M"]', 'Limitar bitrate — 8 Mbps'),
    ('["-maxrate", "8M", "-bufsize", "16M"]', 'Limitar bitrate — 8 Mbps com buffer 16 Mbps'),
    ('["-maxrate", "12M", "-bufsize", "24M"]', 'Limitar bitrate — 12 Mbps com buffer 24 Mbps'),
]
SUBTITLE_FONT_CHOICES = [
    ('Arial', 'Arial'),
    ('Helvetica', 'Helvetica'),
    ('Verdana', 'Verdana'),
    ('Tahoma', 'Tahoma'),
    ('Trebuchet MS', 'Trebuchet MS'),
    ('Georgia', 'Georgia'),
    ('Times New Roman', 'Times New Roman'),
    ('Courier New', 'Courier New'),
    ('Montserrat', 'Montserrat'),
    ('Poppins', 'Poppins'),
    ('Inter', 'Inter'),
    ('Roboto', 'Roboto'),
    ('Open Sans', 'Open Sans'),
    ('Lato', 'Lato'),
    ('Bebas Neue', 'Bebas Neue'),
    ('Impact', 'Impact'),
]


class RenderPresetChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        if obj.width and obj.height:
            divisor = gcd(obj.width, obj.height)
            ratio = f'{obj.width // divisor}:{obj.height // divisor}'
            return f'{obj.name} ({ratio} · {obj.width}x{obj.height})'
        return f'{obj.name} (sem redimensionar)'


class BackgroundMusicChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.get_category_display()} · {obj.name}'


class ColorLUTChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class MasteringProfileChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.name} ({obj.target_lufs} LUFS · {obj.true_peak_db} dBTP)'


class AdminMediaTemplateForm(forms.ModelForm):
    class Meta:
        model = MediaTemplate
        fields = ['name', 'description', 'is_active']
        labels = {
            'name': 'Nome do template',
            'description': 'Descrição',
            'is_active': 'Template ativo',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name == 'is_active':
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})

    def save(self, commit=True):
        instance = super().save(commit=False)
        slug = slugify(self.cleaned_data.get('name', ''))
        if not slug:
            raise forms.ValidationError('Informe um nome para gerar o slug.')
        instance.slug = slug
        instance.category = MediaTemplate.Category.OTHER
        if commit:
            instance.save()
        return instance


class AdminMediaTemplateVersionForm(forms.ModelForm):
    background_music = BackgroundMusicChoiceField(
        label='Trilha de fundo',
        queryset=BackgroundMusicTrack.objects.none(),
        required=False,
        empty_label='Sem trilha',
    )
    mastering_profile = MasteringProfileChoiceField(
        label='Perfil de masterização',
        queryset=MasteringProfile.objects.none(),
        required=False,
        empty_label='Sem masterização',
    )
    color_lut = ColorLUTChoiceField(
        label='LUT de cor', queryset=ColorLUT.objects.none(), required=False, empty_label='Sem LUT',
    )
    audio_mixing_config_raw = forms.CharField(
        label='Configuração avançada de mixagem (JSON)',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text=(
            'Opcional. Ajusta o ducking automático. Exemplo: '
            '{"attack_ms": 140, "hold_ms": 300, "release_ms": 850, "base_duck_db": 11}'
        ),
    )
    dialogue_processing_config_raw = forms.CharField(
        label='Configuração avançada de tratamento de diálogo (JSON)',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text=(
            'Opcional. Ajusta EQ, compressão e nivelamento entre falantes. Exemplo: '
            '{"compression_ratio": 2.5, "leveling_max_gain_db": 6, "deesser_enabled": true}'
        ),
    )
    audio_noise_cleanup_config_raw = forms.CharField(
        label='Configuração avançada de limpeza de ruído (JSON)',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text=(
            'Opcional. Exemplo: '
            '{"global_mode": "LIGHT", "detect_transient_noise": true, "transient_action": "REVIEW"}'
        ),
    )
    LANGUAGE_MODE_SINGLE = 'single'
    LANGUAGE_MODE_TRANSLATED = 'translated'
    LANGUAGE_MODE_BILINGUAL_SOURCE = 'bilingual_source'
    LANGUAGE_MODE_CHOICES = [
        (LANGUAGE_MODE_SINGLE, 'Apenas um idioma'),
        (LANGUAGE_MODE_TRANSLATED, 'Idioma padrão + legendas traduzidas'),
        (LANGUAGE_MODE_BILINGUAL_SOURCE, 'Dois idiomas falados no mesmo vídeo'),
    ]

    preset = RenderPresetChoiceField(
        label='Preset de renderização',
        queryset=RenderPreset.objects.none(),
    )
    language_mode = forms.ChoiceField(
        label='Tipo de idioma do vídeo',
        choices=LANGUAGE_MODE_CHOICES,
        initial=LANGUAGE_MODE_TRANSLATED,
    )
    spoken_languages = forms.MultipleChoiceField(
        label='Idiomas falados no vídeo',
        choices=[('pt', 'Português'), ('en', 'Inglês')],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text='Use quando o vídeo tiver pessoas falando em mais de um idioma.',
    )
    translated_language = forms.ChoiceField(
        label='Idioma traduzido',
        choices=[('', 'Sem tradução'), ('en', 'Inglês'), ('pt', 'Português')],
        required=False,
    )
    advanced_plugins = forms.MultipleChoiceField(
        label='Processamentos extras',
        choices=ADVANCED_PLUGIN_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    auto_reframe_priority = forms.ChoiceField(
        label='Modo de enquadramento',
        choices=[
            ('static', 'Melhorar enquadramento - zoom sutil, sem acompanhar pessoas'),
            ('face', 'Rosto — ideal para sermões e falas'),
            ('body', 'Corpo — ideal para apresentações e movimento'),
        ],
        initial='face',
        required=False,
    )
    speech_edit_profile = forms.ChoiceField(
        label='Perfil de ritmo',
        choices=[
            ('conservative', 'Conservador — sermões e estudos bíblicos'),
            ('balanced', 'Equilibrado — anúncios e vídeos institucionais'),
            ('dynamic', 'Dinâmico — Reels, Shorts e TikTok'),
        ],
        initial='balanced',
        required=False,
    )
    filler_terms = forms.ModelMultipleChoiceField(
        label='Vícios de fala ativos',
        queryset=SpeechFillerTerm.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text='Selecione os termos que este template poderá remover com segurança.',
    )
    default_settings_raw = forms.CharField(
        label='Configurações padrão em JSON',
        required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Opcional. Exemplo: {"max_chars": 42}',
    )
    allowed_overrides_raw = forms.CharField(
        label='Campos liberados para o membro em JSON',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Opcional. Exemplo: ["subtitle_pt", "music"]',
    )

    class Meta:
        model = MediaTemplateVersion
        fields = [
            'preset', 'interactive_preview_enabled', 'preview_proxy_profile',
            'subtitles_enabled', 'subtitle_style', 'translated_subtitle_style',
            'original_language',
            'color_lut', 'lut_file', 'lut_intensity', 'background_music',
            'dialogue_processing_enabled',
            'audio_noise_cleanup_enabled',
            'audio_mixing_enabled', 'audio_ducking_enabled', 'audio_spectral_ducking_enabled',
            'audio_mastering_enabled', 'mastering_profile',
        ]
        labels = {
            'preset': 'Preset de renderização',
            'interactive_preview_enabled': 'Revisão interativa antes da renderização',
            'preview_proxy_profile': 'Qualidade do preview',
            'subtitles_enabled': 'Gerar legendas no vídeo',
            'subtitle_style': 'Estilo da legenda original',
            'translated_subtitle_style': 'Estilo da legenda traduzida',
            'original_language': 'Idioma padrão da legenda',
            'color_lut': 'LUT de cor',
            'lut_file': 'Arquivo LUT legado',
            'lut_intensity': 'Intensidade do LUT',
            'background_music': 'Trilha de fundo',
            'dialogue_processing_enabled': 'Tratamento de diálogo',
            'audio_noise_cleanup_enabled': 'Limpeza de ruído',
            'audio_mixing_enabled': 'Mixagem inteligente',
            'audio_ducking_enabled': 'Ducking automático',
            'audio_spectral_ducking_enabled': 'Ducking espectral',
            'audio_mastering_enabled': 'Masterização',
        }
        widgets = {
            'lut_file': forms.ClearableFileInput(),
            'lut_intensity': forms.NumberInput(attrs={'min': 0, 'max': 100, 'step': 1}),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        is_new_instance = not instance or not instance.pk
        data = kwargs.get('data')
        if data is None and args:
            data = args[0]
        # A few internal/admin clients predate the subtitles checkbox. Preserve
        # the historical enabled behavior when their POST does not include them.
        if data is not None:
            missing = [
                name for name in ('subtitles_enabled',)
                if name not in data
            ]
            if missing:
                data = data.copy()
                for name in missing:
                    enabled = getattr(instance, name, True) if instance else True
                    if enabled:
                        data[name] = 'on'
                if 'data' in kwargs:
                    kwargs['data'] = data
                else:
                    args = (data, *args[1:])
        initial = kwargs.setdefault('initial', {})
        if is_new_instance:
            # The model defaults preserve compatibility for existing templates,
            # while new templates should opt in to subtitle generation explicitly.
            initial.setdefault('subtitles_enabled', False)
        if instance:
            initial.setdefault('default_settings_raw', json.dumps(instance.default_settings or {}, indent=2, ensure_ascii=False))
            initial.setdefault('allowed_overrides_raw', json.dumps(instance.allowed_overrides or [], indent=2, ensure_ascii=False))
            initial.setdefault('audio_mixing_config_raw', json.dumps(instance.audio_mixing_config or {}, indent=2, ensure_ascii=False))
            initial.setdefault(
                'dialogue_processing_config_raw',
                json.dumps(instance.dialogue_processing_config or {}, indent=2, ensure_ascii=False),
            )
            initial.setdefault(
                'audio_noise_cleanup_config_raw',
                json.dumps(instance.audio_noise_cleanup_config or {}, indent=2, ensure_ascii=False),
            )
            initial.setdefault(
                'language_mode',
                (instance.default_settings or {}).get('language_mode', self.LANGUAGE_MODE_TRANSLATED),
            )
            initial.setdefault(
                'spoken_languages',
                (instance.default_settings or {}).get('spoken_languages') or [instance.original_language],
            )
            translated = next(
                (language for language in instance.output_languages if language != instance.original_language),
                '',
            )
            initial.setdefault('translated_language', translated)
            if instance.pk:
                initial.setdefault(
                    'advanced_plugins',
                    list(
                        instance.plugins.filter(
                            code__in=ADVANCED_PLUGIN_CODES,
                            is_enabled=True,
                        ).values_list('code', flat=True)
                    ),
                )
                auto_reframe = instance.plugins.filter(
                    code=MediaTemplatePlugin.Code.AUTO_TRACKING,
                ).first()
                if auto_reframe:
                    initial.setdefault(
                        'auto_reframe_priority',
                        (auto_reframe.configuration or {}).get('priority', 'face'),
                    )
                speech_plugin = instance.plugins.filter(
                    code__in=[
                        MediaTemplatePlugin.Code.SILENCE_REMOVAL,
                        MediaTemplatePlugin.Code.FILLER_REMOVAL,
                    ],
                ).first()
                if speech_plugin:
                    speech_configuration = speech_plugin.configuration or {}
                    initial.setdefault('speech_edit_profile', speech_configuration.get('profile', 'balanced'))
                    configured_words = speech_configuration.get('filler_words') or []
                    if configured_words and not instance.filler_terms.exists():
                        initial.setdefault(
                            'filler_terms',
                            SpeechFillerTerm.objects.filter(
                                is_active=True,
                                language=instance.original_language,
                                text__in=configured_words,
                            ),
                        )
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name in {'spoken_languages', 'advanced_plugins'}:
                continue
            if field_name in {
                'subtitles_enabled', 'translated_subtitles_enabled', 'interactive_preview_enabled',
                'dialogue_processing_enabled',
                'audio_noise_cleanup_enabled',
                'audio_mixing_enabled', 'audio_ducking_enabled',
                'audio_spectral_ducking_enabled', 'audio_mastering_enabled',
            }:
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})
        preset_qs = RenderPreset.objects.filter(is_active=True).order_by('name')
        list(preset_qs)
        subtitle_styles_qs = SubtitleStyle.objects.filter(is_active=True).order_by('name')
        list(subtitle_styles_qs)
        translated_styles_qs = SubtitleStyle.objects.filter(is_active=True).order_by('name')
        list(translated_styles_qs)
        background_music_qs = BackgroundMusicTrack.objects.exclude(audio_file='').order_by('category', 'name')
        list(background_music_qs)
        color_lut_qs = ColorLUT.objects.filter(is_active=True).exclude(lut_file='').order_by('name')
        list(color_lut_qs)
        mastering_profile_qs = MasteringProfile.objects.filter(is_active=True).order_by('name')
        list(mastering_profile_qs)
        filler_language = (getattr(instance, 'original_language', None) or 'pt')
        filler_terms_qs = SpeechFillerTerm.objects.filter(
            is_active=True,
            language=filler_language,
        ).order_by('text')
        list(filler_terms_qs)
        self.fields['preset'].queryset = preset_qs
        self.fields['preview_proxy_profile'].queryset = ProxyProfile.objects.filter(is_active=True).order_by('-is_default', 'name')
        self.fields['subtitle_style'].queryset = subtitle_styles_qs
        self.fields['translated_subtitle_style'].queryset = translated_styles_qs
        self.fields['translated_subtitle_style'].required = False
        self.fields['subtitle_style'].required = False
        # Older template submissions do not contain this newly introduced field.
        # Keep them valid and adopt the recommended intensity automatically.
        self.fields['lut_intensity'].required = False
        self.fields['lut_intensity'].initial = self.instance.lut_intensity if self.instance.pk else 50
        self.fields['background_music'].queryset = background_music_qs
        self.fields['color_lut'].queryset = color_lut_qs
        self.fields['mastering_profile'].queryset = mastering_profile_qs
        self.fields['filler_terms'].queryset = filler_terms_qs
        if instance and instance.pk and instance.filler_terms.exists():
            self.fields['filler_terms'].initial = instance.filler_terms.filter(is_active=True)
        elif not instance or not instance.pk:
            self.fields['filler_terms'].initial = filler_terms_qs
        if is_new_instance:
            self.fields['spoken_languages'].initial = ['pt']
            preview_profile = ProxyProfile.objects.filter(is_active=True, is_default=True).first()
            if preview_profile:
                self.fields['preview_proxy_profile'].initial = preview_profile.pk
            default_profile = MasteringProfile.objects.filter(is_active=True, is_default=True).first()
            if default_profile:
                self.fields['mastering_profile'].initial = default_profile.pk

    def clean_default_settings_raw(self):
        return self._parse_json(self.cleaned_data.get('default_settings_raw'), {}, 'Configurações padrão')

    def clean_lut_intensity(self):
        value = self.cleaned_data.get('lut_intensity')
        return 50 if value in (None, '') else value

    def clean_allowed_overrides_raw(self):
        value = self._parse_json(self.cleaned_data.get('allowed_overrides_raw'), [], 'Campos liberados')
        if not isinstance(value, list):
            raise forms.ValidationError('Use uma lista JSON, por exemplo ["music"].')
        return value

    def clean_audio_mixing_config_raw(self):
        value = self._parse_json(self.cleaned_data.get('audio_mixing_config_raw'), {}, 'Configuração de mixagem')
        if not isinstance(value, dict):
            raise forms.ValidationError('Use um objeto JSON, por exemplo {"base_duck_db": 8}.')
        return value

    def clean_dialogue_processing_config_raw(self):
        value = self._parse_json(
            self.cleaned_data.get('dialogue_processing_config_raw'), {}, 'Configuração de tratamento de diálogo',
        )
        if not isinstance(value, dict):
            raise forms.ValidationError('Use um objeto JSON, por exemplo {"compression_ratio": 2.5}.')
        return value

    def clean_audio_noise_cleanup_config_raw(self):
        value = self._parse_json(
            self.cleaned_data.get('audio_noise_cleanup_config_raw'), {}, 'Configuração de limpeza de ruído',
        )
        if not isinstance(value, dict):
            raise forms.ValidationError('Use um objeto JSON, por exemplo {"global_mode": "LIGHT"}.')
        return value

    @staticmethod
    def _parse_json(raw, default, label):
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f'{label} precisa ser um JSON válido.') from exc

    def save(self, commit=True):
        instance = super().save(commit=False)
        default_settings = dict(self.cleaned_data.get('default_settings_raw') or {})
        language_mode = self.cleaned_data.get('language_mode') or self.LANGUAGE_MODE_TRANSLATED
        default_settings['language_mode'] = language_mode
        spoken_languages = self.cleaned_data.get('spoken_languages') or [instance.original_language]
        if language_mode in (self.LANGUAGE_MODE_SINGLE, self.LANGUAGE_MODE_TRANSLATED):
            spoken_languages = [instance.original_language]
        subtitles_enabled = bool(self.cleaned_data.get('subtitles_enabled'))
        translated_language = self.cleaned_data.get('translated_language') or ''
        translated_enabled = (
            subtitles_enabled
            and language_mode == self.LANGUAGE_MODE_TRANSLATED
            and bool(translated_language)
            and translated_language != instance.original_language
        )
        output_languages = [instance.original_language]
        if (
            translated_enabled
            and
            language_mode != self.LANGUAGE_MODE_SINGLE
            and translated_language
            and translated_language != instance.original_language
        ):
            output_languages.append(translated_language)
        if not translated_enabled or language_mode == self.LANGUAGE_MODE_SINGLE:
            translated_language = ''
            instance.translated_subtitle_style = None
        elif not self.cleaned_data.get('translated_subtitle_style'):
            instance.translated_subtitle_style = instance.subtitle_style
        if not subtitles_enabled:
            instance.subtitle_style = None
            instance.translated_subtitle_style = None
            translated_language = ''
            output_languages = [instance.original_language]
        instance.subtitles_enabled = subtitles_enabled
        instance.translated_subtitles_enabled = translated_enabled
        default_settings['spoken_languages'] = spoken_languages
        default_settings['translated_language'] = translated_language
        instance.output_languages = output_languages
        instance.default_settings = default_settings
        instance.allowed_overrides = self.cleaned_data.get('allowed_overrides_raw') or []
        instance.audio_mixing_config = self.cleaned_data.get('audio_mixing_config_raw') or {}
        instance.dialogue_processing_config = self.cleaned_data.get('dialogue_processing_config_raw') or {}
        instance.audio_noise_cleanup_config = self.cleaned_data.get('audio_noise_cleanup_config_raw') or {}
        if commit:
            instance.save()
        return instance

    def sync_advanced_plugins(self, instance):
        selected_codes = set(self.cleaned_data.get('advanced_plugins') or [])
        selected_filler_terms = list(self.cleaned_data.get('filler_terms') or [])
        # Backwards compatibility for older administrative clients. The current
        # interface uses checkbox vocabulary, but an old POST may still contain
        # the comma-separated field while a template is being upgraded.
        legacy_filler_words = []
        if not selected_filler_terms and self.data.get('filler_words'):
            legacy_filler_words = [
                item.strip()
                for item in self.data.get('filler_words', '').split(',')
                if item.strip()
            ]
        instance.filler_terms.set(selected_filler_terms)
        existing = {
            plugin.code: plugin
            for plugin in instance.plugins.filter(code__in=ADVANCED_PLUGIN_CODES)
        }
        for order, (code, _label) in enumerate(ADVANCED_PLUGIN_CHOICES, start=1):
            plugin = existing.get(code)
            configuration = plugin.configuration or {} if plugin else {}
            if code == MediaTemplatePlugin.Code.AUTO_TRACKING:
                reframe_priority = self.cleaned_data.get('auto_reframe_priority') or 'face'
                is_static_frame = reframe_priority == 'static'
                configuration = {
                    **configuration,
                    'priority': reframe_priority,
                    'safe_margin': 0.18 if reframe_priority == 'face' else 0.15,
                    'top_margin': 0.02 if reframe_priority == 'face' else 0.12,
                    'interval_frames': 10,
                    'smoothing': 0.18,
                    'horizontal_smoothing': 0.34 if reframe_priority == 'face' else 0.18,
                    # Podcasts often benefit from a stable, modest crop without
                    # shifting the frame as speakers move.
                    'static_zoom': 1.06 if is_static_frame else 1.0,
                }
            elif code in {
                MediaTemplatePlugin.Code.SILENCE_REMOVAL,
                MediaTemplatePlugin.Code.FILLER_REMOVAL,
            }:
                filler_words = [
                    term.text.strip()
                    for term in selected_filler_terms
                    if term.text.strip()
                ] or legacy_filler_words
                configuration = {
                    **configuration,
                    'profile': self.cleaned_data.get('speech_edit_profile') or 'balanced',
                    'filler_words': filler_words,
                    'word_safety_margin_ms': 100,
                }
            if plugin:
                plugin.order = order
                plugin.is_enabled = code in selected_codes
                plugin.user_can_override = False
                plugin.configuration = configuration
                plugin.save(update_fields=['order', 'is_enabled', 'user_can_override', 'configuration', 'update_at'])
            else:
                MediaTemplatePlugin.objects.create(
                    version=instance,
                    code=code,
                    order=order,
                    is_enabled=code in selected_codes,
                    user_can_override=False,
                    configuration=configuration,
                )


class AdminMediaTemplateBlockForm(forms.ModelForm):
    overlay_definitions_raw = forms.CharField(
        label='Elementos visuais automáticos',
        required=False,
        widget=forms.HiddenInput(),
        help_text=(
            'Configure os elementos que devem aparecer automaticamente neste bloco. '
            'O conteúdo será solicitado ao membro no projeto.'
        ),
    )

    class Meta:
        model = MediaTemplateBlock
        fields = [
            'key', 'name', 'description', 'order', 'is_required', 'allows_multiple',
            'min_occurrences', 'max_occurrences', 'skip_extra_processing', 'remove_background_voice', 'default_video',
        ]
        labels = {
            'name': 'Nome do bloco',
            'description': 'Descrição',
            'order': 'Posição no vídeo',
            'default_video': 'Vídeo fixo deste bloco',
            'is_required': 'Obrigatório',
            'skip_extra_processing': 'Manter este bloco intacto',
            'remove_background_voice': 'Remover voz de fundo / entrevistador',
            'allows_multiple': 'Aceitar mais de um vídeo',
            'min_occurrences': 'Mínimo de vídeos',
            'max_occurrences': 'Máximo de vídeos',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2}),
            'default_video': forms.ClearableFileInput(attrs={'accept': 'video/*'}),
            'key': forms.HiddenInput(),
            'order': forms.HiddenInput(),
            'allows_multiple': forms.HiddenInput(),
            'min_occurrences': forms.HiddenInput(),
            'max_occurrences': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.setdefault('initial', {})
        if instance:
            initial.setdefault(
                'overlay_definitions_raw',
                json.dumps(instance.overlay_definitions or [], indent=2, ensure_ascii=False),
            )
        super().__init__(*args, **kwargs)
        self.fields['key'].required = False
        self.fields['allows_multiple'].required = False
        for field_name in ('name', 'order', 'min_occurrences', 'max_occurrences'):
            self.fields[field_name].required = False
        for field_name, field in self.fields.items():
            if field_name in ('is_required', 'skip_extra_processing', 'remove_background_voice', 'DELETE'):
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            elif field_name in ('allows_multiple', 'min_occurrences', 'max_occurrences'):
                continue
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})
        if not self.instance.pk and self.initial.get('allows_multiple') is None:
            self.fields['allows_multiple'].initial = True
        if not self.instance.pk and not self.initial.get('min_occurrences'):
            self.fields['min_occurrences'].initial = 1
        if not self.instance.pk:
            self.fields['max_occurrences'].initial = 0

    def has_changed(self):
        if self.instance.pk or not self.is_bound:
            return super().has_changed()
        prefix = self.add_prefix('name')
        name = (self.data.get(prefix, '') or '').strip()
        if not name:
            return False
        return super().has_changed()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('DELETE'):
            return cleaned_data
        name = (cleaned_data.get('name') or '').strip()
        if not name:
            return cleaned_data
        if not cleaned_data.get('key'):
            cleaned_data['key'] = slugify(name)
        if not self.instance.pk:
            cleaned_data['allows_multiple'] = True
        # Os blocos não possuem mais teto de uploads. O campo continua oculto
        # para manter compatibilidade com registros e migrations anteriores.
        cleaned_data['allows_multiple'] = True
        cleaned_data['max_occurrences'] = 0
        raw = cleaned_data.get('overlay_definitions_raw') or ''
        try:
            definitions = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError as exc:
            self.add_error('overlay_definitions_raw', 'Informe uma lista JSON válida.')
            return cleaned_data
        if not isinstance(definitions, list):
            self.add_error('overlay_definitions_raw', 'Os overlays precisam estar em uma lista JSON.')
            return cleaned_data
        valid_types = set(OverlayPreset.Type.values)
        used_keys = set()
        for index, definition in enumerate(definitions, start=1):
            if not isinstance(definition, dict):
                self.add_error('overlay_definitions_raw', f'O overlay {index} precisa ser um objeto.')
                continue
            key = slugify(str(definition.get('key') or ''))
            if not key or key in used_keys:
                self.add_error('overlay_definitions_raw', f'O overlay {index} precisa de uma chave única.')
            used_keys.add(key)
            definition['key'] = key
            if definition.get('type') not in valid_types:
                self.add_error('overlay_definitions_raw', f'O tipo do overlay {index} não é suportado.')
            preset_code = definition.get('preset')
            if not preset_code:
                self.add_error('overlay_definitions_raw', f'Selecione um preset visual para o overlay {index}.')
            elif not OverlayPreset.objects.filter(code=preset_code, is_active=True).exists():
                self.add_error('overlay_definitions_raw', f'O preset "{preset_code}" não existe ou está inativo.')
            schema = definition.get('fields') or {}
            if not isinstance(schema, dict):
                self.add_error('overlay_definitions_raw', f'Os campos do overlay {index} precisam ser um objeto.')
        cleaned_data['_overlay_definitions'] = definitions
        return cleaned_data

    def save(self, commit=True):
        if not (self.cleaned_data.get('name') or '').strip():
            return self.instance
        instance = super().save(commit=False)
        instance.key = self.cleaned_data.get('key') or slugify(self.cleaned_data.get('name', ''))
        if not instance.pk:
            instance.allows_multiple = True
        instance.allows_multiple = True
        instance.max_occurrences = 0
        instance.overlay_definitions = self.cleaned_data.get('_overlay_definitions') or []
        if commit:
            instance.save()
        return instance


class AdminOverlayPresetForm(forms.ModelForm):
    """Friendly editor for the visual decisions shared by overlay declarations."""

    POSITION_CHOICES = [
        ('bottom-center', 'Embaixo, centralizado'),
        ('bottom-right', 'Embaixo, à direita'),
        ('bottom-left', 'Embaixo, à esquerda'),
        ('center', 'No centro'),
    ]
    ANIMATION_CHOICES = [
        ('NONE', 'Sem animação'), ('FADE', 'Aparecer suavemente'),
        ('SLIDE_UP', 'Subir'), ('SLIDE_DOWN', 'Descer'),
        ('SLIDE_LEFT', 'Entrar pela esquerda'), ('SLIDE_RIGHT', 'Entrar pela direita'),
        ('POP', 'Pop'),
    ]
    TIMING_CHOICES = [
        ('BLOCK_START', 'No início do bloco'),
        ('BLOCK_END', 'No final do bloco'),
        ('AUTO_BEST_MOMENT', 'No melhor momento do bloco'),
    ]
    position_choice = forms.ChoiceField(label='Posição', choices=POSITION_CHOICES)
    animation_type = forms.ChoiceField(label='Animação', choices=ANIMATION_CHOICES)
    duration_seconds = forms.DecimalField(label='Duração (segundos)', min_value=Decimal('0.25'), max_value=120, decimal_places=2, initial=5)
    width_percent = forms.IntegerField(label='Largura do elemento (%)', min_value=10, max_value=90, initial=30)
    font = forms.ChoiceField(label='Tipografia', choices=[
        ('Montserrat-Bold.ttf', 'Montserrat Bold'),
        ('Montserrat-SemiBold.ttf', 'Montserrat SemiBold'),
        ('Montserrat-Regular.ttf', 'Montserrat Regular'),
    ])
    font_size = forms.IntegerField(label='Tamanho do texto', min_value=12, max_value=160, initial=42)
    text_color = forms.CharField(label='Cor do texto', initial='#111827', widget=forms.TextInput(attrs={'type': 'color'}))
    background_color = forms.CharField(label='Cor do cartão', initial='#FFFFFF', widget=forms.TextInput(attrs={'type': 'color'}))
    background_opacity = forms.IntegerField(label='Opacidade do cartão (%)', min_value=0, max_value=100, initial=100)
    padding = forms.IntegerField(label='Espaço interno', min_value=0, max_value=120, initial=28)
    border_radius = forms.IntegerField(label='Arredondamento dos cantos', min_value=0, max_value=120, initial=18)
    qr_color = forms.CharField(label='Cor do QR Code', initial='#111111', widget=forms.TextInput(attrs={'type': 'color'}))
    qr_background = forms.CharField(label='Fundo do QR Code', initial='#FFFFFF', widget=forms.TextInput(attrs={'type': 'color'}))
    card_layout = forms.ChoiceField(label='Montagem do QR Code', choices=[
        ('vertical', 'Chamada em cima e QR Code abaixo'),
        ('horizontal', 'QR Code ao lado da chamada'),
    ], initial='vertical')

    class Meta:
        model = OverlayPreset
        fields = ['name', 'overlay_type', 'timing_mode', 'is_active']
        labels = {
            'name': 'Nome do preset', 'overlay_type': 'Tipo de elemento',
            'timing_mode': 'Quando aparece', 'is_active': 'Disponível para uso',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        position = self.instance.position or {}
        known_positions = {
            'bottom-center': {'x': .5, 'y': .82, 'width': .48},
            'bottom-right': {'x': .86, 'y': .76, 'width': .18},
            'bottom-left': {'x': .18, 'y': .76, 'width': .28},
            'center': {'x': .5, 'y': .5, 'width': .4},
        }
        self.fields['position_choice'].initial = next((key for key, value in known_positions.items() if all(abs(position.get(axis, value[axis]) - value[axis]) < .01 for axis in ('x', 'y'))), 'bottom-center')
        self.fields['animation_type'].initial = (self.instance.animation or {}).get('type', 'NONE')
        self.fields['duration_seconds'].initial = (self.instance.duration_ms or 5000) / 1000
        style = self.instance.style or {}
        self.fields['width_percent'].initial = round(float(position.get('width') or .3) * 100)
        for field_name, default in {
            'font': 'Montserrat-Bold.ttf', 'font_size': 42, 'text_color': '#111827',
            'background_color': '#FFFFFF', 'padding': 28, 'border_radius': 18,
            'qr_color': '#111111', 'qr_background': '#FFFFFF', 'card_layout': 'vertical',
        }.items():
            self.fields[field_name].initial = style.get(field_name, default)
        self.fields['background_opacity'].initial = round(float(style.get('background_opacity', 1)) * 100)
        for field in self.fields.values():
            field.widget.attrs.update({'class': FIELD_CLASS})
        self.fields['is_active'].widget.attrs.update({'class': CHECKBOX_CLASS})

    def clean_name(self):
        return self.cleaned_data['name'].strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.code:
            base = slugify(instance.name)[:72] or 'preset-visual'
            code = base
            suffix = 2
            while OverlayPreset.objects.exclude(pk=instance.pk).filter(code=code).exists():
                code = f'{base[:72-len(str(suffix))-1]}-{suffix}'
                suffix += 1
            instance.code = code
        positions = {
            'bottom-center': {'x': .5, 'y': .82, 'width': .48},
            'bottom-right': {'x': .86, 'y': .76, 'width': .18},
            'bottom-left': {'x': .18, 'y': .76, 'width': .28},
            'center': {'x': .5, 'y': .5, 'width': .4},
        }
        instance.position = {**positions[self.cleaned_data['position_choice']], 'width': self.cleaned_data['width_percent'] / 100}
        instance.animation = {'type': self.cleaned_data['animation_type'], 'duration': .35, 'easing': 'ease-out'}
        instance.duration_ms = int(self.cleaned_data['duration_seconds'] * 1000)
        instance.style = {
            **(instance.style or {}),
            'font': self.cleaned_data['font'],
            'font_size': self.cleaned_data['font_size'],
            'color': self.cleaned_data['text_color'],
            'background': self.cleaned_data['background_color'],
            'background_opacity': self.cleaned_data['background_opacity'] / 100,
            'padding': self.cleaned_data['padding'],
            'border_radius': self.cleaned_data['border_radius'],
            'qr_color': self.cleaned_data['qr_color'],
            'qr_background': self.cleaned_data['qr_background'],
            'card_layout': self.cleaned_data['card_layout'],
        }
        if commit:
            instance.save()
        return instance


class AdminMediaTemplateBlockFormSetBase(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for form in self.extra_forms:
            form.empty_permitted = True

    def save_new(self, form, commit=True):
        if not (form.cleaned_data.get('name') or '').strip():
            return None
        return super().save_new(form, commit=commit)

    def clean(self):
        keys = set()
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if form.cleaned_data.get('DELETE'):
                continue
            name = (form.cleaned_data.get('name') or '').strip()
            if not name:
                continue
            base_key = slugify(name) or 'bloco'
            key = self._unique_key(base_key, keys)
            keys.add(key)
            form.cleaned_data['key'] = key
            form.instance.key = key

    @staticmethod
    def _unique_key(base_key, used_keys):
        max_length = MediaTemplateBlock._meta.get_field('key').max_length
        base_key = base_key[:max_length].strip('-') or 'bloco'
        key = base_key
        index = 2
        while key in used_keys:
            suffix = f'-{index}'
            key = f'{base_key[:max_length - len(suffix)].strip("-")}{suffix}'
            index += 1
        return key


class AdminMediaTemplatePluginForm(forms.ModelForm):
    class Meta:
        model = MediaTemplatePlugin
        fields = ['code', 'order', 'is_enabled', 'user_can_override', 'configuration']
        widgets = {'configuration': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_value = self.instance.code if self.instance.pk else None
        filtered_choices = [
            choice for choice in self.fields['code'].choices
            if choice[0] in ADVANCED_PLUGIN_CODES
        ]
        if current_value and current_value not in ADVANCED_PLUGIN_CODES:
            filtered_choices.insert(0, (current_value, self.instance.get_code_display()))
        self.fields['code'].choices = filtered_choices
        for field_name, field in self.fields.items():
            if field_name in ('is_enabled', 'user_can_override', 'DELETE'):
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})
        self.fields['configuration'].initial = self.fields['configuration'].initial or {}


class AdminSpeechFillerTermForm(forms.ModelForm):
    class Meta:
        model = SpeechFillerTerm
        fields = ['text', 'language', 'is_active']
        labels = {
            'text': 'Vício de fala',
            'language': 'Idioma',
            'is_active': 'Disponível para templates',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            field.widget.attrs.update({
                'class': CHECKBOX_CLASS if field_name == 'is_active' else FIELD_CLASS,
            })

    def clean_text(self):
        text = ' '.join((self.cleaned_data.get('text') or '').split())
        if not text:
            raise forms.ValidationError('Informe o termo que deve ser reconhecido.')
        duplicate = SpeechFillerTerm.objects.filter(
            language=self.cleaned_data.get('language') or 'pt',
            text__iexact=text,
        ).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError('Esse termo já está cadastrado para este idioma.')
        return text


class AdminRenderPresetForm(forms.ModelForm):
    video_codec = forms.ChoiceField(
        label='Codec de vídeo',
        choices=RENDER_PRESET_VIDEO_CODEC_CHOICES,
        initial='libx264',
        help_text='Define o formato de compressão do vídeo final.',
    )
    audio_codec = forms.ChoiceField(
        label='Codec de áudio',
        choices=RENDER_PRESET_AUDIO_CODEC_CHOICES,
        initial='copy',
        help_text='Copiar mantém o áudio original; AAC reencoda para MP4.',
    )
    video_crf = forms.TypedChoiceField(
        label='CRF',
        choices=RENDER_PRESET_CRF_CHOICES,
        coerce=int,
        initial=23,
        help_text='Controla qualidade vs. tamanho do arquivo. 23 é um bom equilíbrio.',
    )
    extra_ffmpeg_profile = forms.ChoiceField(
        label='Argumentos extras do FFmpeg',
        choices=RENDER_PRESET_EXTRA_FFMPEG_CHOICES,
        required=False,
        initial='[]',
        help_text='Use apenas se precisar limitar bitrate ou ajustes avançados.',
    )

    class Meta:
        model = RenderPreset
        fields = [
            'name', 'width', 'height', 'video_codec', 'audio_codec',
            'video_crf', 'extra_ffmpeg_profile', 'is_active',
        ]
        labels = {
            'name': 'Nome do preset',
            'width': 'Largura',
            'height': 'Altura',
            'is_active': 'Preset ativo',
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.setdefault('initial', {})
        if instance:
            extra_args_json = json.dumps(instance.extra_ffmpeg_args or [], ensure_ascii=False)
            known_extra_values = {value for value, _label in RENDER_PRESET_EXTRA_FFMPEG_CHOICES}
            if extra_args_json not in known_extra_values:
                self.fields['extra_ffmpeg_profile'].choices = [
                    *RENDER_PRESET_EXTRA_FFMPEG_CHOICES,
                    (extra_args_json, 'Configuração personalizada atual'),
                ]
            initial.setdefault('extra_ffmpeg_profile', extra_args_json)
            if instance.video_codec and instance.video_codec not in dict(RENDER_PRESET_VIDEO_CODEC_CHOICES):
                self.fields['video_codec'].choices = [
                    *RENDER_PRESET_VIDEO_CODEC_CHOICES,
                    (instance.video_codec, f'{instance.video_codec} (atual)'),
                ]
            if instance.audio_codec and instance.audio_codec not in dict(RENDER_PRESET_AUDIO_CODEC_CHOICES):
                self.fields['audio_codec'].choices = [
                    *RENDER_PRESET_AUDIO_CODEC_CHOICES,
                    (instance.audio_codec, f'{instance.audio_codec} (atual)'),
                ]
            if instance.video_crf not in dict(RENDER_PRESET_CRF_CHOICES):
                self.fields['video_crf'].choices = [
                    *RENDER_PRESET_CRF_CHOICES,
                    (instance.video_crf, f'{instance.video_crf} (atual)'),
                ]
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name == 'is_active':
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})

    def clean_extra_ffmpeg_profile(self):
        raw = self.cleaned_data.get('extra_ffmpeg_profile') or '[]'
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError('Selecione uma opção válida de argumentos extras.') from exc
        if not isinstance(value, list):
            raise forms.ValidationError('Os argumentos extras precisam ser uma lista.')
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        code = slugify(self.cleaned_data.get('name', ''))
        if not code:
            raise forms.ValidationError('Informe um nome para gerar o código do preset.')
        instance.code = code
        instance.extra_ffmpeg_args = self.cleaned_data.get('extra_ffmpeg_profile') or []
        if commit:
            instance.save()
        return instance


class AdminSubtitleStyleForm(forms.ModelForm):
    font_name = forms.ChoiceField(
        label='Família da fonte',
        choices=SUBTITLE_FONT_CHOICES,
        initial='Arial',
    )
    font_weight = forms.TypedChoiceField(
        label='Peso da fonte',
        choices=SubtitleStyle.FontWeight.choices,
        coerce=int,
        initial=SubtitleStyle.FontWeight.BOLD,
        help_text='SemiBold e acima são gravados como negrito no arquivo ASS.',
    )
    alignment = forms.TypedChoiceField(
        label='Alinhamento',
        choices=SubtitleStyle.Alignment.choices,
        coerce=int,
        initial=SubtitleStyle.Alignment.BOTTOM_CENTER,
    )
    background_opacity = forms.IntegerField(
        label='Opacidade do fundo (%)',
        min_value=0,
        max_value=100,
        initial=70,
    )
    background_padding_x = forms.IntegerField(
        label='Extra de largura (px)',
        min_value=0,
        max_value=200,
        initial=14,
        help_text='0 deixa o fundo no limite horizontal da fonte (incluindo o contorno).',
    )
    background_padding_y = forms.IntegerField(
        label='Extra de altura (px)',
        min_value=0,
        max_value=200,
        initial=8,
        help_text='Espaço extra além da altura do fundo. Use 0 e reduza a “Altura do fundo (%)” para uma faixa mais baixa.',
    )
    background_height_percent = forms.IntegerField(
        label='Altura do fundo (%)',
        min_value=40,
        max_value=100,
        initial=100,
        help_text='100 = altura da fonte. Valores menores deixam a faixa mais baixa que o texto (ex.: 70).',
    )
    shadow = forms.IntegerField(
        label='Distância da sombra',
        min_value=0,
        max_value=80,
        initial=1,
        help_text='Quão longe a sombra fica do texto (0 desliga, salvo se houver tamanho/desfoque).',
    )
    shadow_angle = forms.IntegerField(
        label='Ângulo da sombra (°)',
        min_value=0,
        max_value=360,
        initial=45,
        help_text='0° = direita, 90° = baixo, 180° = esquerda, 270° = cima. 45° é a diagonal clássica.',
    )
    shadow_size = forms.IntegerField(
        label='Tamanho da sombra',
        min_value=0,
        max_value=40,
        initial=0,
        help_text='Aumenta a sombra sem desfocar (escala a letra na camada de sombra). 0 = tamanho normal.',
    )
    shadow_blur = forms.IntegerField(
        label='Desfoque da sombra',
        min_value=0,
        max_value=30,
        initial=0,
        help_text='Suaviza a sombra (\\blur no ASS). Valores altos deixam mais difusa.',
    )
    shadow_opacity = forms.IntegerField(
        label='Opacidade da sombra (%)',
        min_value=0,
        max_value=100,
        initial=70,
        help_text='Controla o alpha da sombra no vídeo final.',
    )

    class Meta:
        model = SubtitleStyle
        fields = [
            'name', 'font_name', 'font_weight', 'font_size', 'primary_color',
            'background_enabled', 'background_color', 'background_opacity',
            'background_padding_x', 'background_padding_y', 'background_height_percent',
            'background_radius',
            'outline_color', 'outline_width',
            'shadow', 'shadow_angle', 'shadow_size', 'shadow_blur', 'shadow_opacity',
            'margin_bottom', 'alignment',
            'max_lines', 'max_characters', 'is_active',
        ]
        labels = {
            'name': 'Nome do estilo',
            'font_name': 'Família da fonte',
            'font_weight': 'Peso da fonte',
            'font_size': 'Tamanho',
            'primary_color': 'Cor do texto',
            'background_enabled': 'Usar plano de fundo',
            'background_color': 'Cor do plano de fundo',
            'background_opacity': 'Opacidade do fundo (%)',
            'background_padding_x': 'Extra de largura (px)',
            'background_padding_y': 'Extra de altura (px)',
            'background_height_percent': 'Altura do fundo (%)',
            'background_radius': 'Arredondamento (preview)',
            'outline_color': 'Cor do contorno',
            'outline_width': 'Espessura do contorno',
            'shadow': 'Distância da sombra',
            'shadow_angle': 'Ângulo da sombra (°)',
            'shadow_size': 'Tamanho da sombra',
            'shadow_blur': 'Desfoque da sombra',
            'shadow_opacity': 'Opacidade da sombra (%)',
            'margin_bottom': 'Margem da borda',
            'alignment': 'Alinhamento',
            'max_lines': 'Máximo de linhas',
            'max_characters': 'Máximo de caracteres',
            'is_active': 'Estilo ativo',
        }
        help_texts = {
            'background_radius': 'O ASS final usa caixa retangular; o arredondamento vale só no preview.',
            'margin_bottom': 'Distância da legenda até a borda do quadro (em pixels do preset).',
            'font_weight': 'SemiBold e acima são gravados como negrito no arquivo ASS.',
            'background_padding_x': '0 deixa o fundo no limite horizontal da fonte (incluindo o contorno).',
            'background_padding_y': 'Espaço extra além da altura do fundo. Use 0 e reduza a “Altura do fundo (%)” para uma faixa mais baixa.',
            'background_height_percent': '100 = altura da fonte. Valores menores deixam a faixa mais baixa que o texto (ex.: 70).',
            'shadow': 'Quão longe a sombra fica do texto (0 desliga, salvo se houver tamanho/desfoque).',
            'shadow_angle': '0° = direita, 90° = baixo, 180° = esquerda, 270° = cima. 45° é a diagonal clássica.',
            'shadow_size': 'Aumenta a sombra sem desfocar (escala tipográfica). 0 = tamanho normal.',
            'shadow_blur': 'Suaviza a sombra. Independente do tamanho — use 0 para sombra nítida.',
            'shadow_opacity': 'Controla o alpha da sombra no vídeo final.',
        }
        widgets = {
            'primary_color': forms.TextInput(attrs={'type': 'color'}),
            'background_color': forms.TextInput(attrs={'type': 'color'}),
            'outline_color': forms.TextInput(attrs={'type': 'color'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name in ('is_active', 'background_enabled'):
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            elif field_name in ('primary_color', 'background_color', 'outline_color'):
                field.widget.attrs.update({'class': COLOR_CLASS})
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})
        for field_name in ('background_opacity', 'shadow_opacity', 'background_height_percent'):
            self.fields[field_name].widget.attrs.update({
                'min': '0' if field_name != 'background_height_percent' else '40',
                'max': '100',
                'step': '1',
            })
        for field_name in ('background_padding_x', 'background_padding_y'):
            self.fields[field_name].widget.attrs.update({'min': '0', 'max': '200', 'step': '1'})
        self.fields['shadow'].widget.attrs.update({'min': '0', 'max': '80', 'step': '1'})
        self.fields['shadow_angle'].widget.attrs.update({'min': '0', 'max': '360', 'step': '1'})
        self.fields['shadow_size'].widget.attrs.update({'min': '0', 'max': '40', 'step': '1'})
        self.fields['shadow_blur'].widget.attrs.update({'min': '0', 'max': '30', 'step': '1'})


    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        queryset = SubtitleStyle.objects.filter(name__iexact=name)
        if self.instance and self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError('Já existe um estilo com esse nome. Use outro nome para salvar a cópia.')
        return name


class AdminBackgroundMusicForm(forms.ModelForm):
    class Meta:
        model = BackgroundMusicTrack
        fields = ['name', 'category', 'tempo', 'audio_file']
        labels = {
            'name': 'Nome da trilha',
            'category': 'Categoria',
            'tempo': 'Andamento',
            'audio_file': 'Arquivo de áudio',
        }
        widgets = {
            'audio_file': forms.ClearableFileInput(attrs={'accept': 'audio/*'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': FIELD_CLASS})
        self.fields['audio_file'].required = not bool(self.instance and self.instance.pk and self.instance.audio_file)


class AdminColorLUTForm(forms.ModelForm):
    class Meta:
        model = ColorLUT
        fields = ['name', 'description', 'default_intensity', 'lut_file', 'is_active']
        labels = {
            'name': 'Nome do LUT', 'description': 'Descrição', 'default_intensity': 'Intensidade padrão (%)',
            'lut_file': 'Arquivo .cube', 'is_active': 'LUT ativo',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'lut_file': forms.ClearableFileInput(attrs={'accept': '.cube'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.update({'class': CHECKBOX_CLASS if name == 'is_active' else FIELD_CLASS})
        self.fields['default_intensity'].widget.attrs.update({'min': 0, 'max': 100, 'step': 1})
        self.fields['lut_file'].required = not bool(self.instance and self.instance.pk and self.instance.lut_file)


class AdminMasteringProfileForm(forms.ModelForm):
    class Meta:
        model = MasteringProfile
        fields = [
            'name', 'target_lufs', 'true_peak_db',
            'dynamic_range_target', 'low_frequency_control', 'high_frequency_control',
            'max_gain_db', 'max_limiter_reduction_db',
            'bus_compression_enabled', 'limiter_enabled', 'is_default', 'is_active',
        ]
        labels = {
            'name': 'Nome do perfil',
            'target_lufs': 'Loudness alvo (LUFS)',
            'true_peak_db': 'True Peak máximo (dBTP)',
            'dynamic_range_target': 'Faixa dinâmica alvo (LU)',
            'low_frequency_control': 'Ajuste de graves (dB)',
            'high_frequency_control': 'Ajuste de agudos (dB)',
            'max_gain_db': 'Ganho máximo permitido (dB)',
            'max_limiter_reduction_db': 'Limiting máximo permitido (dB)',
            'bus_compression_enabled': 'Compressão de bus leve',
            'limiter_enabled': 'Limiter de true peak',
            'is_default': 'Perfil padrão',
            'is_active': 'Perfil ativo',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        optional_defaults = {
            'dynamic_range_target': 11.0,
            'low_frequency_control': 0.0,
            'high_frequency_control': 0.0,
            'max_gain_db': 12.0,
            'max_limiter_reduction_db': 4.0,
        }
        for field_name, field in self.fields.items():
            if field_name in {'bus_compression_enabled', 'limiter_enabled', 'is_default', 'is_active'}:
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})
            if field_name in optional_defaults:
                field.required = False
                field.initial = optional_defaults[field_name]

    def clean(self):
        cleaned_data = super().clean()
        for field_name, default in {
            'dynamic_range_target': 11.0,
            'low_frequency_control': 0.0,
            'high_frequency_control': 0.0,
            'max_gain_db': 12.0,
            'max_limiter_reduction_db': 4.0,
        }.items():
            if cleaned_data.get(field_name) is None:
                cleaned_data[field_name] = default
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.code:
            instance.code = slugify(self.cleaned_data.get('name', ''))
        if commit:
            instance.save()
            if instance.is_default:
                MasteringProfile.objects.exclude(pk=instance.pk).update(is_default=False)
        return instance


AdminMediaTemplateBlockFormSet = inlineformset_factory(
    MediaTemplateVersion,
    MediaTemplateBlock,
    form=AdminMediaTemplateBlockForm,
    formset=AdminMediaTemplateBlockFormSetBase,
    extra=1,
    can_delete=True,
)

AdminMediaTemplatePluginFormSet = inlineformset_factory(
    MediaTemplateVersion,
    MediaTemplatePlugin,
    form=AdminMediaTemplatePluginForm,
    extra=1,
    can_delete=True,
)
