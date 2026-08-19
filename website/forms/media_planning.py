from django import forms

from website.models.event import Event
from website.models.media_attachment import MediaAttachment
from website.models.media_comment import MediaComment
from website.models.media_content import MediaContent
from website.models.media_task import MediaTask
from website.models.user import User

_INPUT = 'form-input'
_SELECT = 'form-input'
_TEXTAREA = (
    'w-full rounded-xl border border-gray-300 px-4 py-3 text-sm '
    'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] '
    'focus:border-transparent resize-none'
)


class MediaContentForm(forms.ModelForm):
    class Meta:
        model = MediaContent
        fields = [
            'title', 'description', 'content_type', 'event',
            'publication_date', 'due_date', 'responsible', 'status', 'priority',
            'sub_team', 'assigned_role', 'requires_recording', 'requires_editing',
            'publication_channel', 'observations',
        ]
        widgets = {
            'title': forms.TextInput(attrs={
                'class': _INPUT,
                'placeholder': 'Ex: Arte do culto de domingo',
            }),
            'description': forms.Textarea(attrs={
                'class': _TEXTAREA,
                'placeholder': 'Descreva o conteúdo, referências ou observações...',
                'rows': 3,
            }),
            'content_type': forms.Select(attrs={'class': _SELECT}),
            'event': forms.Select(attrs={'class': _SELECT}),
            'publication_date': forms.DateTimeInput(
                attrs={'class': _INPUT, 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'due_date': forms.DateTimeInput(
                attrs={'class': _INPUT, 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'responsible': forms.Select(attrs={'class': _SELECT}),
            'status': forms.Select(attrs={'class': _SELECT}),
            'priority': forms.Select(attrs={'class': _SELECT}),
            'sub_team': forms.Select(attrs={'class': _SELECT}),
            'assigned_role': forms.Select(attrs={'class': _SELECT}),
            'requires_recording': forms.CheckboxInput(attrs={'class': 'rounded'}),
            'requires_editing': forms.CheckboxInput(attrs={'class': 'rounded'}),
            'publication_channel': forms.Select(attrs={'class': _SELECT}),
            'observations': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from website.models.media_organization import MediaRole, MediaSubTeam
        self.fields['event'].queryset = Event.objects.order_by('-event_date')
        self.fields['event'].empty_label = '— Sem evento vinculado —'
        self.fields['responsible'].queryset = (
            User.objects.filter(member__isnull=False).order_by('member__name')
        )
        self.fields['responsible'].empty_label = '— Selecione o responsável —'
        self.fields['sub_team'].queryset = MediaSubTeam.objects.filter(is_active=True).order_by('name')
        self.fields['sub_team'].empty_label = '— Nenhuma —'
        self.fields['assigned_role'].queryset = MediaRole.objects.order_by('name')
        self.fields['assigned_role'].empty_label = '— Nenhuma —'
        self.fields['publication_date'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['due_date'].input_formats = ['%Y-%m-%dT%H:%M']


class MediaTaskForm(forms.ModelForm):
    class Meta:
        model = MediaTask
        fields = ['title', 'description', 'assigned_to', 'due_date', 'status']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': _INPUT,
                'placeholder': 'Ex: Criar arte base',
            }),
            'description': forms.Textarea(attrs={
                'class': _TEXTAREA,
                'placeholder': 'Detalhes da tarefa...',
                'rows': 2,
            }),
            'assigned_to': forms.Select(attrs={'class': _SELECT}),
            'due_date': forms.DateTimeInput(
                attrs={'class': _INPUT, 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'status': forms.Select(attrs={'class': _SELECT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = (
            User.objects.filter(member__isnull=False).order_by('member__name')
        )
        self.fields['assigned_to'].empty_label = '— Selecionar membro —'
        self.fields['due_date'].input_formats = ['%Y-%m-%dT%H:%M']


class MediaCommentForm(forms.ModelForm):
    class Meta:
        model = MediaComment
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={
                'class': _TEXTAREA,
                'placeholder': 'Escreva um comentário...',
                'rows': 2,
            }),
        }


class MediaAttachmentForm(forms.ModelForm):
    class Meta:
        model = MediaAttachment
        fields = ['name', 'file']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': _INPUT,
                'placeholder': 'Nome do arquivo',
            }),
            'file': forms.ClearableFileInput(attrs={'class': _INPUT}),
        }


_MEDIA_INPUT = 'media-field-input'
_MEDIA_SELECT = 'media-field-select'


class MediaDemandQuickForm(forms.ModelForm):
    """Formulário compacto para criar demanda no hub."""

    class Meta:
        model = MediaContent
        fields = ['title', 'description', 'content_type']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': _MEDIA_INPUT,
                'placeholder': 'Ex: Arte principal do culto',
            }),
            'description': forms.Textarea(attrs={
                'class': _MEDIA_INPUT,
                'rows': 3,
                'placeholder': 'Detalhes, referências ou contexto da demanda',
            }),
            'content_type': forms.Select(attrs={'class': _MEDIA_SELECT}),
        }

    def __init__(self, *args, **kwargs):
        from website.services.demands_hub import (
            DEMAND_TYPES_ORGANIZATIONAL,
            DEMAND_TYPES_TECHNICAL,
        )
        super().__init__(*args, **kwargs)
        self.fields['content_type'].choices = [
            ('', 'Selecione o tipo'),
            ('Produção de conteúdo', [(t['value'], t['label']) for t in DEMAND_TYPES_TECHNICAL]),
            ('Organização e alinhamento', [(t['value'], t['label']) for t in DEMAND_TYPES_ORGANIZATIONAL]),
        ]
        self.fields['content_type'].widget.attrs['id'] = (
            f'id_{self.prefix}-content_type' if self.prefix else 'id_demand-content_type'
        )
        self.fields['description'].required = False


class MediaEventQuickForm(forms.Form):
    """Formulário compacto para criar evento no hub."""

    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': _MEDIA_INPUT,
            'placeholder': 'Ex: Conferência de Mulheres',
        }),
    )
    event_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': _MEDIA_INPUT, 'type': 'date'}),
    )
    event_time = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={'class': _MEDIA_INPUT, 'type': 'time'}),
    )
    location = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': _MEDIA_INPUT,
            'placeholder': 'Ex: Templo principal',
        }),
    )
    event_type = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label='Sem template',
        widget=forms.Select(attrs={'class': _MEDIA_SELECT}),
    )
    apply_template = forms.BooleanField(
        required=False,
        initial=True,
        label='Aplicar template de mídia do tipo selecionado',
        widget=forms.CheckboxInput(attrs={'class': 'demands-modal-checkbox'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from website.models.media_event_type import MediaEventType
        self.fields['event_type'].queryset = (
            MediaEventType.objects.filter(is_active=True).order_by('sort_order', 'name')
        )
        prefix = self.prefix or 'event'
        self.fields['title'].widget.attrs['id'] = f'id_{prefix}-title'
        self.fields['event_date'].widget.attrs['id'] = f'id_{prefix}-event_date'
        self.fields['event_time'].widget.attrs['id'] = f'id_{prefix}-event_time'
        self.fields['location'].widget.attrs['id'] = f'id_{prefix}-location'
        self.fields['event_type'].widget.attrs['id'] = f'id_{prefix}-event_type'
        self.fields['apply_template'].widget.attrs['id'] = f'id_{prefix}-apply_template'
        self.fields['apply_template'].widget.attrs['class'] = 'demands-modal-checkbox'


class MediaEventTypeQuickForm(forms.Form):
    """Criação rápida de tipo de evento + template no hub."""

    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': _MEDIA_INPUT,
            'placeholder': 'Ex: Conferência, Batismo, Culto...',
        }),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': _MEDIA_INPUT,
            'rows': 2,
            'placeholder': 'Descreva o tipo de evento (opcional)',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        prefix = self.prefix or 'event_type'
        self.fields['name'].widget.attrs['id'] = f'id_{prefix}-name'
        self.fields['description'].widget.attrs['id'] = f'id_{prefix}-description'

    def clean_name(self):
        from website.models.media_event_type import MediaEventType
        name = self.cleaned_data['name'].strip()
        if MediaEventType.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError('Já existe um tipo com este nome.')
        return name

