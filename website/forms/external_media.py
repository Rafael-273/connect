from pathlib import Path

from django import forms
from django.conf import settings
from django.db.models import OuterRef, Subquery

from ..models.external_media import (
    ExternalMediaJob,
    ExternalMediaProject,
    GlossaryTerm,
    MasteringProfile,
    MediaTemplateVersion,
    ProjectBlockMedia,
    ProjectCustomBlock,
    RenderPreset,
    SubtitleStyle,
    VideoMasteringJob,
)


VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}


def validate_video_upload(video):
    if Path(video.name).suffix.lower() not in VIDEO_EXTENSIONS:
        raise forms.ValidationError('Formato não suportado. Envie MP4, MOV, MKV, WEBM, AVI ou M4V.')
    max_bytes = settings.EXTERNAL_MEDIA_MAX_UPLOAD_MB * 1024 * 1024
    if video.size > max_bytes:
        raise forms.ValidationError(
            f'O vídeo excede o limite de {settings.EXTERNAL_MEDIA_MAX_UPLOAD_MB} MB.'
        )
    return video


class ExternalMediaJobForm(forms.ModelForm):
    output_languages = forms.MultipleChoiceField(
        label='Idiomas de saída',
        choices=[('pt', 'Português'), ('en', 'Inglês')],
        initial=['pt', 'en'],
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = ExternalMediaJob
        fields = [
            'name', 'original_video', 'original_language', 'output_languages',
            'preset', 'subtitle_style',
        ]
        labels = {
            'name': 'Nome do vídeo',
            'original_video': 'Upload de vídeo',
            'original_language': 'Idioma original',
            'preset': 'Formato de saída',
            'subtitle_style': 'Estilo da legenda',
        }
        widgets = {
            'original_video': forms.ClearableFileInput(attrs={'accept': 'video/*'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['preset'].queryset = RenderPreset.objects.filter(is_active=True)
        self.fields['subtitle_style'].queryset = SubtitleStyle.objects.filter(is_active=True)
        self.fields['original_language'].choices = [('pt', 'Português'), ('en', 'Inglês')]

    def clean_original_video(self):
        return validate_video_upload(self.cleaned_data['original_video'])


class ExternalMediaProjectForm(forms.ModelForm):
    template_version = forms.ModelChoiceField(
        label='Template', queryset=MediaTemplateVersion.objects.none(), empty_label='Selecione um template',
    )

    class Meta:
        model = ExternalMediaProject
        fields = ['template_version', 'name']
        labels = {'name': 'Nome do projeto'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        latest = MediaTemplateVersion.objects.filter(
            template=OuterRef('template'),
        ).order_by('-version').values('pk')[:1]
        self.fields['template_version'].queryset = (
            MediaTemplateVersion.objects.filter(
                template__is_active=True,
                pk=Subquery(latest),
            ).select_related('template').order_by('template__name')
        )


class ExternalMediaProjectEditForm(forms.ModelForm):
    """Campos seguros para alterar depois que o projeto já foi criado.

    O template não pode mudar aqui: blocos, uploads e decisões de processamento
    pertencem ao template escolhido na criação do projeto.
    """

    class Meta:
        model = ExternalMediaProject
        fields = ['name']
        labels = {'name': 'Nome do projeto'}
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full rounded-xl border border-gray-300 px-4 py-3',
                'maxlength': 180,
            }),
        }


class ProjectBlockMediaForm(forms.ModelForm):
    trim_start_seconds = forms.DecimalField(required=False, min_value=0, decimal_places=3, max_digits=12)
    trim_end_seconds = forms.DecimalField(required=False, min_value=0, decimal_places=3, max_digits=12)
    camera_role = forms.ChoiceField(
        choices=ProjectBlockMedia.CameraRole.choices,
        required=False,
        initial=ProjectBlockMedia.CameraRole.PRIMARY,
    )
    camera_label = forms.CharField(required=False, max_length=80)
    camera_key = forms.CharField(required=False, max_length=80)
    camera_hint = forms.ChoiceField(
        choices=ProjectBlockMedia.CameraHint.choices,
        required=False,
        initial=ProjectBlockMedia.CameraHint.AUTO,
    )

    class Meta:
        model = ProjectBlockMedia
        fields = ['file', 'camera_role', 'camera_label', 'camera_hint']
        labels = {
            'file': 'Vídeo',
            'camera_label': 'Nome da câmera',
            'camera_hint': 'Enquadramento',
        }
        widgets = {'file': forms.ClearableFileInput(attrs={'accept': 'video/*'})}

    def clean_file(self):
        return validate_video_upload(self.cleaned_data['file'])

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get('trim_start_seconds')
        end = cleaned_data.get('trim_end_seconds')
        if start is not None and end is not None and end <= start:
            raise forms.ValidationError('O tempo final do corte precisa ser maior que o tempo inicial.')
        return cleaned_data

    @staticmethod
    def seconds_to_ms(value):
        if value in (None, ''):
            return None
        return max(0, round(float(value) * 1000))


class ProjectCustomBlockForm(forms.ModelForm):
    class Meta:
        model = ProjectCustomBlock
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full rounded-xl border border-gray-300 px-3 py-2', 'placeholder': 'Nome do bloco'}),
            'description': forms.TextInput(attrs={'class': 'w-full rounded-xl border border-gray-300 px-3 py-2', 'placeholder': 'Descrição opcional'}),
        }


class ExternalMediaProjectSettingsForm(forms.Form):
    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        configured = project.configuration.get('plugins', {})
        for plugin in project.template_version.plugins.filter(is_enabled=True, user_can_override=True):
            self.fields[f'plugin_{plugin.code}'] = forms.BooleanField(
                label=plugin.get_code_display(),
                required=False,
                initial=configured.get(plugin.code, True),
            )

    def save(self):
        configuration = dict(self.project.configuration)
        plugins = dict(configuration.get('plugins', {}))
        for name, value in self.cleaned_data.items():
            if name.startswith('plugin_'):
                plugins[name.removeprefix('plugin_')] = value
        configuration['plugins'] = plugins
        self.project.configuration = configuration
        self.project.save(update_fields=['configuration', 'update_at'])
        return self.project


class GlossaryTermForm(forms.ModelForm):
    class Meta:
        model = GlossaryTerm
        fields = ['source_language', 'target_language', 'source_text', 'translated_text']
        labels = {
            'source_language': 'Idioma original',
            'target_language': 'Idioma de destino',
            'source_text': 'Termo original',
            'translated_text': 'Tradução oficial',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [('pt', 'Português'), ('en', 'Inglês')]
        self.fields['source_language'].choices = choices
        self.fields['target_language'].choices = choices

    def clean_source_text(self):
        return (self.cleaned_data.get('source_text') or '').strip()

    def clean_translated_text(self):
        return (self.cleaned_data.get('translated_text') or '').strip()

    def clean(self):
        cleaned = super().clean()
        source_language = cleaned.get('source_language')
        target_language = cleaned.get('target_language')
        source_text = cleaned.get('source_text')
        if source_language and target_language and source_text:
            existing = GlossaryTerm.objects.filter(
                source_language=source_language,
                target_language=target_language,
                source_text=source_text,
            )
            if self.instance and self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                self.add_error(
                    'source_text',
                    'Este termo já existe para este par de idiomas. Edite ou remova o termo existente.',
                )
        return cleaned


class VideoMasteringUploadForm(forms.ModelForm):
    class Meta:
        model = VideoMasteringJob
        fields = ['name', 'original_video']
        labels = {'name': 'Nome do vídeo', 'original_video': 'Arquivo de vídeo'}
        widgets = {
            'original_video': forms.ClearableFileInput(attrs={'accept': '.mp4,.mov,video/mp4,video/quicktime'}),
        }

    def clean_original_video(self):
        video = self.cleaned_data['original_video']
        extension = Path(video.name).suffix.lower()
        if extension not in {'.mp4', '.mov'}:
            raise forms.ValidationError('Envie um vídeo MP4 ou MOV.')
        return validate_video_upload(video)


class VideoMasteringProfileForm(forms.Form):
    mastering_profile = forms.ModelChoiceField(
        label='Perfil de masterização',
        queryset=MasteringProfile.objects.none(),
        empty_label=None,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['mastering_profile'].queryset = MasteringProfile.objects.filter(
            is_active=True,
        ).order_by('-is_default', 'name')
