import json
from math import gcd

from django import forms
from django.forms.models import BaseInlineFormSet
from django.forms import inlineformset_factory
from django.utils.text import slugify

from ..models.external_media import (
    MediaTemplate,
    MediaTemplateBlock,
    MediaTemplatePlugin,
    MediaTemplateVersion,
    RenderPreset,
    SubtitleStyle,
)
from ..models.music import Music


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
    background_music = forms.ModelChoiceField(
        label='Música padrão',
        queryset=Music.objects.none(),
        required=False,
        empty_label='Sem música',
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
        label='Prioridade do enquadramento',
        choices=[
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
    filler_words = forms.CharField(
        label='Vícios de fala reconhecidos',
        initial='eh, é, hum, hmm, ahn, ah, hã, tipo, né, então, assim',
        required=False,
        help_text='Separe por vírgulas. A palavra só será removida quando estiver isolada e houver margem segura.',
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
            'preset', 'subtitle_style', 'translated_subtitle_style', 'original_language',
            'lut_file', 'background_music',
        ]
        labels = {
            'preset': 'Preset de renderização',
            'subtitle_style': 'Estilo da legenda original',
            'translated_subtitle_style': 'Estilo da legenda traduzida',
            'original_language': 'Idioma padrão da legenda',
            'lut_file': 'Arquivo LUT',
            'background_music': 'Música de fundo',
        }
        widgets = {
            'lut_file': forms.ClearableFileInput(),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.setdefault('initial', {})
        if instance:
            initial.setdefault('default_settings_raw', json.dumps(instance.default_settings or {}, indent=2, ensure_ascii=False))
            initial.setdefault('allowed_overrides_raw', json.dumps(instance.allowed_overrides or [], indent=2, ensure_ascii=False))
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
                    if speech_configuration.get('filler_words'):
                        initial.setdefault('filler_words', ', '.join(speech_configuration['filler_words']))
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name in {'spoken_languages', 'advanced_plugins'}:
                continue
            field.widget.attrs.update({'class': FIELD_CLASS})
        preset_qs = RenderPreset.objects.filter(is_active=True).order_by('name')
        list(preset_qs)
        subtitle_styles_qs = SubtitleStyle.objects.filter(is_active=True).order_by('name')
        list(subtitle_styles_qs)
        translated_styles_qs = SubtitleStyle.objects.filter(is_active=True).order_by('name')
        list(translated_styles_qs)
        background_music_qs = Music.objects.exclude(audio_file='').order_by('name', 'singer')
        list(background_music_qs)
        self.fields['preset'].queryset = preset_qs
        self.fields['subtitle_style'].queryset = subtitle_styles_qs
        self.fields['translated_subtitle_style'].queryset = translated_styles_qs
        self.fields['translated_subtitle_style'].required = False
        self.fields['background_music'].queryset = background_music_qs
        if not instance:
            self.fields['spoken_languages'].initial = ['pt']

    def clean_default_settings_raw(self):
        return self._parse_json(self.cleaned_data.get('default_settings_raw'), {}, 'Configurações padrão')

    def clean_allowed_overrides_raw(self):
        value = self._parse_json(self.cleaned_data.get('allowed_overrides_raw'), [], 'Campos liberados')
        if not isinstance(value, list):
            raise forms.ValidationError('Use uma lista JSON, por exemplo ["music"].')
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
        translated_language = self.cleaned_data.get('translated_language') or ''
        output_languages = [instance.original_language]
        if (
            language_mode != self.LANGUAGE_MODE_SINGLE
            and translated_language
            and translated_language != instance.original_language
        ):
            output_languages.append(translated_language)
        if language_mode == self.LANGUAGE_MODE_SINGLE:
            translated_language = ''
            instance.translated_subtitle_style = None
        elif not self.cleaned_data.get('translated_subtitle_style'):
            instance.translated_subtitle_style = instance.subtitle_style
        default_settings['spoken_languages'] = spoken_languages
        default_settings['translated_language'] = translated_language
        instance.output_languages = output_languages
        instance.default_settings = default_settings
        instance.allowed_overrides = self.cleaned_data.get('allowed_overrides_raw') or []
        if commit:
            instance.save()
        return instance

    def sync_advanced_plugins(self, instance):
        selected_codes = set(self.cleaned_data.get('advanced_plugins') or [])
        existing = {
            plugin.code: plugin
            for plugin in instance.plugins.filter(code__in=ADVANCED_PLUGIN_CODES)
        }
        for order, (code, _label) in enumerate(ADVANCED_PLUGIN_CHOICES, start=1):
            plugin = existing.get(code)
            configuration = plugin.configuration or {} if plugin else {}
            if code == MediaTemplatePlugin.Code.AUTO_TRACKING:
                configuration = {
                    **configuration,
                    'priority': self.cleaned_data.get('auto_reframe_priority') or 'face',
                    'safe_margin': 0.15,
                    'top_margin': 0.18,
                    'interval_frames': 10,
                    'smoothing': 0.18,
                }
            elif code in {
                MediaTemplatePlugin.Code.SILENCE_REMOVAL,
                MediaTemplatePlugin.Code.FILLER_REMOVAL,
            }:
                filler_words = [
                    value.strip()
                    for value in (self.cleaned_data.get('filler_words') or '').split(',')
                    if value.strip()
                ]
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
    class Meta:
        model = MediaTemplateBlock
        fields = [
            'key', 'name', 'description', 'order', 'is_required', 'allows_multiple',
            'min_occurrences', 'max_occurrences', 'skip_extra_processing', 'default_video',
        ]
        labels = {
            'name': 'Nome do bloco',
            'description': 'Descrição',
            'order': 'Posição no vídeo',
            'default_video': 'Vídeo fixo deste bloco',
            'is_required': 'Obrigatório',
            'skip_extra_processing': 'Manter este bloco intacto',
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
        super().__init__(*args, **kwargs)
        self.fields['key'].required = False
        self.fields['allows_multiple'].required = False
        for field_name in ('name', 'order', 'min_occurrences', 'max_occurrences'):
            self.fields[field_name].required = False
        for field_name, field in self.fields.items():
            if field_name in ('is_required', 'skip_extra_processing', 'DELETE'):
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            elif field_name in ('allows_multiple', 'min_occurrences', 'max_occurrences'):
                continue
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})
        if not self.instance.pk and self.initial.get('allows_multiple') is None:
            self.fields['allows_multiple'].initial = True
        if not self.instance.pk and not self.initial.get('min_occurrences'):
            self.fields['min_occurrences'].initial = 1
        if not self.instance.pk and not self.initial.get('max_occurrences'):
            self.fields['max_occurrences'].initial = 4

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
        if not cleaned_data.get('allows_multiple'):
            cleaned_data['min_occurrences'] = 1
            cleaned_data['max_occurrences'] = 1
        return cleaned_data

    def save(self, commit=True):
        if not (self.cleaned_data.get('name') or '').strip():
            return self.instance
        instance = super().save(commit=False)
        instance.key = self.cleaned_data.get('key') or slugify(self.cleaned_data.get('name', ''))
        if not instance.pk:
            instance.allows_multiple = True
        if not self.cleaned_data.get('allows_multiple'):
            instance.min_occurrences = 1
            instance.max_occurrences = 1
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


class AdminRenderPresetForm(forms.ModelForm):
    extra_ffmpeg_args_raw = forms.CharField(
        label='Argumentos extras do FFmpeg',
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Use uma lista JSON. Exemplo: ["-maxrate", "8M"]',
    )

    class Meta:
        model = RenderPreset
        fields = [
            'name', 'width', 'height', 'video_codec', 'audio_codec',
            'video_crf', 'extra_ffmpeg_args_raw', 'is_active',
        ]
        labels = {
            'name': 'Nome do preset',
            'width': 'Largura',
            'height': 'Altura',
            'video_codec': 'Codec de vídeo',
            'audio_codec': 'Codec de áudio',
            'video_crf': 'CRF',
            'is_active': 'Preset ativo',
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.setdefault('initial', {})
        if instance:
            initial.setdefault('extra_ffmpeg_args_raw', json.dumps(instance.extra_ffmpeg_args or [], indent=2))
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if field_name == 'is_active':
                field.widget.attrs.update({'class': CHECKBOX_CLASS})
            else:
                field.widget.attrs.update({'class': FIELD_CLASS})

    def clean_extra_ffmpeg_args_raw(self):
        raw = self.cleaned_data.get('extra_ffmpeg_args_raw')
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError('Informe uma lista JSON válida.') from exc
        if not isinstance(value, list):
            raise forms.ValidationError('Use uma lista JSON, por exemplo ["-maxrate", "8M"].')
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        code = slugify(self.cleaned_data.get('name', ''))
        if not code:
            raise forms.ValidationError('Informe um nome para gerar o código do preset.')
        instance.code = code
        instance.extra_ffmpeg_args = self.cleaned_data.get('extra_ffmpeg_args_raw') or []
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


class BackgroundMusicChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        if obj.singer:
            return f'{obj.name} - {obj.singer}'
        return obj.name


class AdminBackgroundMusicForm(forms.ModelForm):
    class Meta:
        model = Music
        fields = ['name', 'singer', 'tempo', 'audio_file']
        labels = {
            'name': 'Nome da música',
            'singer': 'Artista ou ministério',
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
