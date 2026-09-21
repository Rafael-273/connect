from django import forms

from website.services.ministry_organization import media_teams, media_ministry, scoped_roles

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
            'start_date', 'publication_date', 'due_date', 'responsible', 'status', 'priority',
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
            'start_date': forms.DateTimeInput(attrs={'class': _INPUT, 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
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
            'sub_team': forms.Select(attrs={'class': _SELECT, 'data-demand-team': 'true'}),
            'assigned_role': forms.Select(attrs={'class': _SELECT}),
            'requires_recording': forms.CheckboxInput(attrs={'class': 'rounded'}),
            'requires_editing': forms.CheckboxInput(attrs={'class': 'rounded'}),
            'publication_channel': forms.Select(attrs={'class': _SELECT}),
            'observations': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['event'].queryset = Event.objects.filter(is_recurring=False).order_by('-event_date')
        self.fields['event'].empty_label = '— Sem evento vinculado —'
        from website.services.demands_hub import get_responsible_picker_options
        self.fields['responsible'].queryset = get_responsible_picker_options()
        self.fields['responsible'].empty_label = '— Selecione o responsável —'
        self.fields['sub_team'].queryset = media_teams().filter(is_active=True).order_by('name')
        self.fields['sub_team'].empty_label = '— Nenhuma —'
        self.fields['assigned_role'].queryset = scoped_roles(media_ministry()).order_by('name')
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

    def __init__(self, *args, content=None, **kwargs):
        super().__init__(*args, **kwargs)
        from website.services.demands_hub import get_responsible_picker_options
        self.fields['assigned_to'].queryset = get_responsible_picker_options()
        if content:
            self.instance.content = content
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
        fields = ['title', 'description', 'content_type', 'sub_team']
        widgets = {
            'sub_team': forms.Select(attrs={'class': _MEDIA_SELECT, 'data-demand-team': 'true'}),
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
        if self.is_bound and not hasattr(self.data, 'getlist'):
            from django.http import QueryDict
            data = QueryDict('', mutable=True)
            for key, value in self.data.items():
                values = value if isinstance(value, (list, tuple)) else [value]
                data.setlist(key, [str(item) if item is not None else '' for item in values])
            self.data = data
        technical_choices = [(t['value'], t['label']) for t in DEMAND_TYPES_TECHNICAL]
        organizational_choices = [(t['value'], t['label']) for t in DEMAND_TYPES_ORGANIZATIONAL]
        quick_values = {value for value, _ in technical_choices + organizational_choices}
        if self.instance and self.instance.pk:
            current = self.instance.content_type
            if current and current not in quick_values:
                label = self.instance.get_content_type_display()
                technical_choices.append((current, f'{label} (formato anterior)'))
        self.fields['content_type'].choices = [
            ('', 'Selecione o tipo'),
            ('Produção de conteúdo', technical_choices),
            ('Organização e alinhamento', organizational_choices),
        ]
        self.fields['content_type'].widget.attrs['id'] = (
            f'id_{self.prefix}-content_type' if self.prefix else 'id_demand-content_type'
        )
        self.fields['description'].required = False
        self.fields['sub_team'].queryset = media_teams().filter(is_active=True)
        self.fields['sub_team'].empty_label = '— Sem equipe —'

    def clean(self):
        data = super().clean()
        prefix = self.prefix or 'demand'
        for field in ('assignment_user', 'assignment_id'):
            if any(value and (not value.isascii() or not value.isdigit() or len(value) > 18) for value in self.data.getlist(f'{prefix}-{field}')):
                self.add_error(None, 'Há uma atribuição inválida. Selecione novamente o responsável.')
        due_days = self.data.getlist(f'{prefix}-assignment_due_days')
        due_relations = self.data.getlist(f'{prefix}-assignment_due_relation')
        for i, days_raw in enumerate(due_days):
            relation = due_relations[i] if i < len(due_relations) else 'before'
            if relation == 'on':
                continue
            if days_raw in (None, ''):
                continue
            try:
                days = int(days_raw)
            except (TypeError, ValueError):
                self.add_error(None, 'Informe uma quantidade válida de dias para cada etapa.')
                break
            if days < 0:
                self.add_error(None, 'A quantidade de dias deve ser zero ou maior.')
                break
        if any(len(value) > 200 for value in self.data.getlist(f'{prefix}-assignment_role')):
            self.add_error(None, 'O papel de uma etapa deve ter até 200 caracteres.')
        return data

    def _post_clean(self):
        # The hub derives this field from the submitted steps after validating all of them.
        self.instance.responsible = None
        from website.services.demands_hub import parse_demand_assignments
        self.instance._replacement_assignee_ids = [
            row['user_id'] for row in parse_demand_assignments(self.data, prefix=self.prefix or 'demand')
        ]
        selected_team = self.cleaned_data.get('sub_team')
        if self.instance.sub_team_id != getattr(selected_team, 'pk', None):
            self.instance.assigned_role = None
        super()._post_clean()


class MediaEventQuickForm(forms.Form):
    """Formulário compacto para criar evento no hub."""

    title = forms.CharField(
        label='Título',
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': _MEDIA_INPUT,
            'placeholder': 'Ex: Conferência de Mulheres',
        }),
    )
    event_date = forms.DateField(
        label='Data de início',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': _MEDIA_INPUT, 'type': 'date'}),
    )
    event_time = forms.TimeField(
        label='Horário de início',
        required=False,
        widget=forms.TimeInput(attrs={'class': _MEDIA_INPUT, 'type': 'time'}),
    )
    description = forms.CharField(required=False, label='Descrição', widget=forms.Textarea(attrs={'class': _MEDIA_INPUT, 'rows': 3}))
    end_date = forms.DateField(required=False, label='Data de término', widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': _MEDIA_INPUT, 'type': 'date'}))
    end_time = forms.TimeField(required=False, label='Horário de término', widget=forms.TimeInput(attrs={'class': _MEDIA_INPUT, 'type': 'time'}))
    location = forms.CharField(
        label='Local',
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            'class': _MEDIA_INPUT,
            'placeholder': 'Ex: Templo principal',
        }),
    )
    event_type = forms.ModelChoiceField(
        label='Tipo de evento',
        queryset=None,
        required=False,
        empty_label='Sem template',
        widget=forms.Select(attrs={'class': _MEDIA_SELECT}),
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
        self.fields['description'].widget.attrs['id'] = f'id_{prefix}-description'
        self.fields['location'].widget.attrs['id'] = f'id_{prefix}-location'
        self.fields['event_type'].widget.attrs['id'] = f'id_{prefix}-event_type'

    def clean(self):
        data = super().clean()
        start, end = data.get('event_date'), data.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'O término deve ser igual ou posterior ao início.')
        if data.get('end_time') and not end:
            self.add_error('end_date', 'Informe a data de término.')
        if start and end == start and data.get('event_time') and data.get('end_time') and data['end_time'] < data['event_time']:
            self.add_error('end_time', 'O horário de término deve ser posterior ao início.')
        return data


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
