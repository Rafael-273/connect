from django import forms

from website.models.event import Event
from website.models.media_event_type import MediaEventType, MediaPlanningTemplate, MediaPlanningTemplateItem
from website.models.media_organization import (
    LEADERSHIP_CATEGORY_CHOICES,
    LEADERSHIP_PRIORITY_CHOICES,
    LEADERSHIP_STATUS_CHOICES,
    RESOURCE_CATEGORY_CHOICES,
    SUBSCRIPTION_TYPE_CHOICES,
    MediaLeadershipItem,
    MediaResource,
    MediaResourceCredential,
    MediaRole,
    MediaSubTeam,
    MediaSubTeamMembership,
)
from website.models.member import Member

_INPUT = 'form-input'
_SELECT = 'form-input'
_TEXTAREA = (
    'w-full rounded-xl border border-gray-300 px-4 py-3 text-sm '
    'focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)] '
    'focus:border-transparent resize-none'
)


class MediaEventTypeForm(forms.ModelForm):
    class Meta:
        model = MediaEventType
        fields = ['name', 'description', 'is_active', 'sort_order']
        widgets = {
            'name': forms.TextInput(attrs={'class': _INPUT, 'placeholder': 'Ex: Conferência'}),
            'description': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'rounded'}),
            'sort_order': forms.NumberInput(attrs={'class': _INPUT}),
        }


class MediaTemplateUnifiedForm(forms.Form):
    """Formulário unificado: tipo de evento = template de mídia."""

    name = forms.CharField(
        max_length=100,
        label='Nome',
        widget=forms.TextInput(attrs={'class': _INPUT, 'placeholder': 'Ex: Conferência, Batismo...'}),
    )
    description = forms.CharField(
        required=False,
        label='Descrição',
        widget=forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
    )
    is_active = forms.BooleanField(required=False, initial=True, label='Ativo')
    sort_order = forms.IntegerField(
        required=False,
        initial=0,
        label='Ordem',
        widget=forms.NumberInput(attrs={'class': _INPUT}),
    )

    def __init__(self, *args, event_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event_type = event_type
        if event_type:
            self.fields['name'].initial = event_type.name
            tpl = getattr(event_type, 'planning_template', None)
            self.fields['description'].initial = (
                tpl.description if tpl else event_type.description
            )
            self.fields['is_active'].initial = event_type.is_active
            self.fields['sort_order'].initial = event_type.sort_order

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        qs = MediaEventType.objects.filter(name__iexact=name)
        if self.event_type:
            qs = qs.exclude(pk=self.event_type.pk)
        if qs.exists():
            raise forms.ValidationError('Já existe um template com este nome.')
        return name

    def save(self):
        data = self.cleaned_data
        if self.event_type:
            et = self.event_type
            et.name = data['name']
            et.description = data.get('description', '')
            et.is_active = data.get('is_active', True)
            et.sort_order = data.get('sort_order') or 0
            et.save()
            tpl, _ = MediaPlanningTemplate.objects.get_or_create(
                event_type=et,
                defaults={
                    'name': et.name,
                    'description': et.description,
                    'is_active': et.is_active,
                },
            )
            tpl.name = et.name
            tpl.description = data.get('description', '')
            tpl.is_active = data.get('is_active', True)
            tpl.save()
            return tpl

        et = MediaEventType.objects.create(
            name=data['name'],
            description=data.get('description', ''),
            is_active=data.get('is_active', True),
            sort_order=data.get('sort_order') or 0,
        )
        return MediaPlanningTemplate.objects.create(
            event_type=et,
            name=et.name,
            description=et.description,
            is_active=et.is_active,
        )


class MediaPlanningTemplateForm(forms.ModelForm):
    class Meta:
        model = MediaPlanningTemplate
        fields = ['event_type', 'name', 'description', 'is_active']
        widgets = {
            'event_type': forms.Select(attrs={'class': _SELECT}),
            'name': forms.TextInput(attrs={'class': _INPUT}),
            'description': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'rounded'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        used_types = MediaPlanningTemplate.objects.exclude(
            pk=getattr(self.instance, 'pk', None)
        ).values_list('event_type_id', flat=True)
        self.fields['event_type'].queryset = MediaEventType.objects.filter(
            is_active=True
        ).exclude(pk__in=used_types).order_by('sort_order', 'name')


class MediaPlanningTemplateItemForm(forms.ModelForm):
    class Meta:
        model = MediaPlanningTemplateItem
        fields = [
            'title', 'content_type', 'description', 'default_sub_team', 'default_role',
            'requires_recording', 'requires_editing', 'lead_offset_days',
            'due_offset_days', 'publication_offset_days', 'publication_channel',
            'notes', 'sort_order',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': _INPUT}),
            'content_type': forms.Select(attrs={'class': _SELECT}),
            'description': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
            'default_sub_team': forms.Select(attrs={'class': _SELECT}),
            'default_role': forms.Select(attrs={'class': _SELECT}),
            'requires_recording': forms.CheckboxInput(attrs={'class': 'rounded'}),
            'requires_editing': forms.CheckboxInput(attrs={'class': 'rounded'}),
            'lead_offset_days': forms.NumberInput(attrs={'class': _INPUT, 'placeholder': 'Ex: -21'}),
            'due_offset_days': forms.NumberInput(attrs={'class': _INPUT, 'placeholder': 'Ex: 3'}),
            'publication_offset_days': forms.NumberInput(attrs={'class': _INPUT, 'placeholder': 'Ex: -14'}),
            'publication_channel': forms.Select(attrs={'class': _SELECT}),
            'notes': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
            'sort_order': forms.NumberInput(attrs={'class': _INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['default_sub_team'].queryset = MediaSubTeam.objects.filter(
            is_active=True
        ).order_by('name')
        self.fields['default_sub_team'].empty_label = '— Nenhuma —'
        self.fields['default_role'].queryset = MediaRole.objects.order_by('name')
        self.fields['default_role'].empty_label = '— Nenhuma —'


class EventTypeAssignForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ['event_type']
        widgets = {
            'event_type': forms.Select(attrs={'class': _SELECT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['event_type'].queryset = MediaEventType.objects.filter(
            is_active=True
        ).order_by('sort_order', 'name')
        self.fields['event_type'].empty_label = '— Selecionar template —'
        self.fields['event_type'].label = 'Template'


class MediaSubTeamForm(forms.ModelForm):
    class Meta:
        model = MediaSubTeam
        fields = ['name', 'description', 'leader', 'responsibilities', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': _INPUT}),
            'description': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
            'leader': forms.Select(attrs={'class': _SELECT}),
            'responsibilities': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 3}),
            'notes': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'rounded'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['leader'].queryset = _media_members_qs()
        self.fields['leader'].empty_label = '— Selecionar —'


class MediaRoleForm(forms.ModelForm):
    class Meta:
        model = MediaRole
        fields = ['name', 'description', 'sub_team']
        widgets = {
            'name': forms.TextInput(attrs={'class': _INPUT}),
            'description': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
            'sub_team': forms.Select(attrs={'class': _SELECT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sub_team'].queryset = MediaSubTeam.objects.filter(is_active=True).order_by('name')
        self.fields['sub_team'].empty_label = '— Geral —'


class MediaSubTeamMembershipForm(forms.ModelForm):
    class Meta:
        model = MediaSubTeamMembership
        fields = ['member', 'roles', 'responsibilities', 'is_active']
        widgets = {
            'member': forms.Select(attrs={'class': _SELECT}),
            'roles': forms.SelectMultiple(attrs={'class': _SELECT, 'size': 5}),
            'responsibilities': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'rounded'}),
        }

    def __init__(self, *args, sub_team=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sub_team = sub_team
        self.fields['member'].queryset = _media_members_qs()
        roles_qs = MediaRole.objects.all()
        if sub_team:
            roles_qs = roles_qs.filter(sub_team__isnull=True) | roles_qs.filter(sub_team=sub_team)
        self.fields['roles'].queryset = roles_qs.order_by('name')


class MediaLeadershipItemForm(forms.ModelForm):
    class Meta:
        model = MediaLeadershipItem
        fields = [
            'title', 'description', 'category', 'priority', 'status',
            'responsible', 'notes',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': _INPUT}),
            'description': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 3}),
            'category': forms.Select(attrs={'class': _SELECT}),
            'priority': forms.Select(attrs={'class': _SELECT}),
            'status': forms.Select(attrs={'class': _SELECT}),
            'responsible': forms.Select(attrs={'class': _SELECT}),
            'notes': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['responsible'].queryset = _media_members_qs()
        self.fields['responsible'].empty_label = '— Ninguém —'


class MediaResourceForm(forms.ModelForm):
    class Meta:
        model = MediaResource
        fields = [
            'name', 'category', 'url', 'email_username', 'responsible',
            'access_members', 'subscription_type', 'renewal_date', 'notes', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': _INPUT}),
            'category': forms.Select(attrs={'class': _SELECT}),
            'url': forms.URLInput(attrs={'class': _INPUT}),
            'email_username': forms.TextInput(attrs={'class': _INPUT}),
            'responsible': forms.Select(attrs={'class': _SELECT}),
            'access_members': forms.SelectMultiple(attrs={'class': _SELECT, 'size': 5}),
            'subscription_type': forms.Select(attrs={'class': _SELECT}),
            'renewal_date': forms.DateInput(attrs={'class': _INPUT, 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'rounded'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['responsible'].queryset = _media_members_qs()
        self.fields['responsible'].empty_label = '— Selecionar —'
        self.fields['access_members'].queryset = _media_members_qs()


class MediaResourceCredentialForm(forms.Form):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': _INPUT,
            'placeholder': 'Nova senha (deixe em branco para manter)',
            'autocomplete': 'new-password',
        }),
        label='Senha',
    )
    sensitive_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': _TEXTAREA, 'rows': 2, 'placeholder': 'Notas sensíveis'}),
        label='Notas sensíveis',
    )


def _media_members_qs():
    from website.models.ministry_membership import MinistryMembership
    member_ids = MinistryMembership.objects.filter(
        ministry__name__iexact='Mídia Externa',
        is_active=True,
    ).values_list('member_id', flat=True)
    return Member.objects.filter(pk__in=member_ids).order_by('name')
