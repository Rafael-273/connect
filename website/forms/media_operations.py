from django import forms
from website.models import Event, MediaEventType, User
from website.models.media_content import STATUS_CHOICES, PRIORITY_CHOICES
from website.services.ministry_organization import media_teams


class DemandFiltersForm(forms.Form):
    q = forms.CharField(required=False, label='Buscar')
    event = forms.ModelChoiceField(queryset=Event.objects.none(), required=False, label='Evento', empty_label='Todos os eventos')
    kind = forms.ChoiceField(required=False, label='Vínculo', choices=[('', 'Todas as demandas'), ('free', 'Avulsas'), ('event', 'Com evento')])
    team = forms.ModelChoiceField(queryset=Event.objects.none(), required=False, label='Equipe', empty_label='Todas as equipes')
    responsible = forms.ModelChoiceField(queryset=User.objects.none(), required=False, label='Responsável', empty_label='Todos os responsáveis')
    status = forms.ChoiceField(required=False, choices=[('', 'Todos os status')] + STATUS_CHOICES)
    priority = forms.ChoiceField(required=False, label='Prioridade', choices=[('', 'Todas')] + PRIORITY_CHOICES)
    due_from = forms.DateField(required=False, label='Prazo a partir de', widget=forms.DateInput(attrs={'type': 'date'}))
    due_to = forms.DateField(required=False, label='Prazo até', widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['event'].queryset = Event.objects.order_by('-event_date', 'title')
        self.fields['team'].queryset = media_teams().order_by('name')
        self.fields['responsible'].queryset = User.objects.filter(member__isnull=False).order_by('member__name')
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-input'

    def clean(self):
        data = super().clean()
        if data.get('due_from') and data.get('due_to') and data['due_from'] > data['due_to']:
            raise forms.ValidationError('O início do período deve ser anterior ao fim.')
        return data


class EventFiltersForm(forms.Form):
    q = forms.CharField(required=False, label='Buscar')
    event_type = forms.ModelChoiceField(queryset=MediaEventType.objects.all(), required=False, label='Tipo', empty_label='Todos os tipos')
    date_from = forms.DateField(required=False, label='De', widget=forms.DateInput(attrs={'type': 'date'}))
    date_to = forms.DateField(required=False, label='Até', widget=forms.DateInput(attrs={'type': 'date'}))
