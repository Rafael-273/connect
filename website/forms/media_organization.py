from django import forms
from django.db import transaction
from website.models.event import Event
from website.models.media_event_type import MediaEventType, MediaPlanningTemplate, MediaPlanningTemplateItem
from website.models.media_organization import (
    MediaLeadershipItem,
    MediaResource,
    MediaRole,
    MediaSubTeam,
    MediaSubTeamMembership,
)
from website.models.member import Member
from website.services.ministry_organization import media_ministry, media_teams, ministry_members, scoped_roles

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

    def _restore_deleted_event_type(self, data):
        archived = MediaEventType.deleted_objects.filter(name__iexact=data['name']).first()
        if not archived:
            return None
        archived.undelete()
        archived.description = data.get('description', '')
        archived.is_active = data.get('is_active', True)
        archived.sort_order = data.get('sort_order') or 0
        archived.save()
        return archived

    def _template_for_event_type(self, et, data):
        tpl = MediaPlanningTemplate.deleted_objects.filter(event_type=et).first()
        if tpl:
            tpl.undelete()
        else:
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

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        if self.event_type:
            et = self.event_type
            et.name = data['name']
            et.description = data.get('description', '')
            et.is_active = data.get('is_active', True)
            et.sort_order = data.get('sort_order') or 0
            et.save()
            return self._template_for_event_type(et, data)

        et = self._restore_deleted_event_type(data)
        if et is None:
            et = MediaEventType.objects.create(
                name=data['name'],
                description=data.get('description', ''),
                is_active=data.get('is_active', True),
                sort_order=data.get('sort_order') or 0,
            )
        return self._template_for_event_type(et, data)


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


_MEDIA_INPUT = 'media-field-input'
_MEDIA_SELECT = 'media-field-select'


class MediaPlanningTemplateItemForm(forms.ModelForm):
    """Formulário de demanda padrão — mesmo padrão do hub de demandas."""

    class Meta:
        model = MediaPlanningTemplateItem
        fields = ['title', 'content_type', 'description', 'default_sub_team', 'sort_order']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': _MEDIA_INPUT,
                'placeholder': 'Ex: Arte principal do culto',
            }),
            'content_type': forms.Select(attrs={'class': _MEDIA_SELECT}),
            'description': forms.Textarea(attrs={
                'class': _MEDIA_INPUT,
                'rows': 3,
                'placeholder': 'Detalhes, referências ou contexto da demanda',
            }),
            'default_sub_team': forms.Select(attrs={'class': _MEDIA_SELECT, 'data-demand-team': 'true'}),
            'sort_order': forms.HiddenInput(),
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
            f'id_{self.prefix}-content_type' if self.prefix else 'id_content_type'
        )
        self.fields['description'].required = False
        self.fields['default_sub_team'].queryset = media_teams().filter(is_active=True).order_by('name')
        self.fields['default_sub_team'].empty_label = '— Sem equipe —'
        self.fields['default_sub_team'].label = 'Equipe responsável (opcional)'

    def clean(self):
        data = super().clean()
        prefix = self.prefix or 'template'
        for field in ('assignment_user', 'assignment_id'):
            if any(
                value and (not value.isascii() or not value.isdigit() or len(value) > 18)
                for value in self.data.getlist(f'{prefix}-{field}')
            ):
                self.add_error(None, 'Há uma atribuição inválida.')
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

    def __init__(self, *args, ministry=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.ministry = ministry or media_ministry()
        self.fields['leader'].queryset = ministry_members(self.instance.ministry)
        self.fields['leader'].empty_label = '— Selecionar —'

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        existing = MediaSubTeam.all_objects.filter(ministry=self.instance.ministry, name=name)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError('Já existe uma equipe com este nome neste ministério.')
        return name


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
        self.fields['sub_team'].queryset = media_teams().filter(is_active=True).order_by('name')
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
        self.instance.sub_team = sub_team
        if self.instance.pk:
            self.fields['member'].disabled = True
        elif not self.is_bound:
            self.initial.setdefault('is_active', True)
        self.fields['member'].queryset = ministry_members(sub_team.ministry) if sub_team else Member.objects.none()
        roles_qs = scoped_roles(sub_team.ministry) if sub_team else MediaRole.objects.none()
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
    return ministry_members(media_ministry())
