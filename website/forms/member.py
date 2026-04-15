from django import forms
from ..models.member import Member

# ── CSS tokens used by the self-service member area ──
_INPUT = (
    'w-full px-4 py-3 border border-gray-200 rounded-xl bg-gray-50 '
    'focus:bg-white focus:border-gray-300 focus:outline-none transition-all'
)
_TEXTAREA = _INPUT + ' resize-none'

# ── CSS tokens that match the admin edit.html <style> block ──
_A_INPUT    = 'form-input'
_A_SELECT   = 'form-select'
_A_TEXTAREA = 'form-textarea'
_A_CHECKBOX = (
    'form-checkbox h-4 w-4 sm:h-5 sm:w-5 text-[var(--color-primary)] '
    'rounded border-gray-300 focus:ring-[var(--color-primary)]'
)

_USER_TYPE_CHOICES = [
    ('member',        'Membro (Sem acesso ao painel)'),
    ('admin',         'Administrador (Acesso total)'),
    ('visitors',      'Visitantes (Gerencia visitantes)'),
    ('consolidation', 'Consolidação (Gerencia acompanhamentos)'),
    ('events',        'Eventos (Gerencia eventos)'),
]

_OPTIONAL = [
    'phone', 'birth_date', 'gender', 'marital_status', 'neighborhood',
    'conversion', 'conversion_date', 'address', 'testimony',
    'profile_picture', 'email',
]


class MemberAdminForm(forms.ModelForm):
    """Full-featured form used by the admin panel to create/edit a Member."""

    # Extra fields that belong to the linked User model, not Member directly
    email = forms.EmailField(
        label='Email',
        required=False,
        widget=forms.EmailInput(attrs={'class': _A_INPUT}),
    )
    user_type = forms.ChoiceField(
        label='Tipo de Usuário',
        choices=_USER_TYPE_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': _A_SELECT}),
    )

    class Meta:
        model = Member
        fields = [
            'name', 'phone', 'birth_date', 'gender', 'marital_status',
            'neighborhood', 'conversion', 'conversion_date',
            'address', 'profile_picture', 'testimony',
            'is_active', 'is_available_to_consolidate',
            'is_available_to_disciple', 'is_approver',
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': _A_INPUT,
            }),
            'phone': forms.TextInput(attrs={
                'class': _A_INPUT,
                'placeholder': '(11) 99999-9999',
            }),
            'birth_date': forms.DateInput(attrs={
                'class': _A_INPUT,
                'type': 'date',
            }),
            'gender': forms.Select(attrs={'class': _A_SELECT}),
            'marital_status': forms.Select(attrs={'class': _A_SELECT}),
            'neighborhood': forms.Select(attrs={'class': _A_SELECT}),
            'conversion': forms.Select(attrs={'class': _A_SELECT}),
            'conversion_date': forms.DateInput(attrs={
                'class': _A_INPUT,
                'type': 'date',
            }),
            'address': forms.TextInput(attrs={
                'class': _A_INPUT,
                'placeholder': 'Rua, número, bairro, cidade',
            }),
            'profile_picture': forms.ClearableFileInput(attrs={
                'id': 'photo-input',
                'accept': 'image/*',
            }),
            'testimony': forms.Textarea(attrs={
                'class': _A_TEXTAREA,
                'rows': 4,
                'placeholder': 'Compartilhe o testemunho do membro...',
            }),
            'is_active':                   forms.CheckboxInput(attrs={'class': _A_CHECKBOX}),
            'is_available_to_consolidate': forms.CheckboxInput(attrs={'class': _A_CHECKBOX}),
            'is_available_to_disciple':    forms.CheckboxInput(attrs={'class': _A_CHECKBOX}),
            'is_approver':                 forms.CheckboxInput(attrs={'class': _A_CHECKBOX}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Mark optional fields
        for name in _OPTIONAL:
            if name in self.fields:
                self.fields[name].required = False

        # Prepend blank option to CharField-with-choices selects
        for name in ('gender', 'marital_status', 'conversion'):
            self.fields[name].choices = [('', 'Selecionar...')] + list(self.fields[name].choices)

        # ModelChoiceField uses empty_label instead
        self.fields['neighborhood'].empty_label = 'Selecionar...'

        # Default is_active = True for new members
        if not self.instance.pk:
            self.fields['is_active'].initial = True


class MemberProfileForm(forms.ModelForm):
    """Form for member self-service profile editing (member area)."""

    class Meta:
        model = Member
        fields = ['name', 'birth_date', 'phone', 'address', 'neighborhood', 'testimony']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': _INPUT,
                'placeholder': 'Seu nome completo',
            }),
            'birth_date': forms.DateInput(attrs={
                'type': 'date',
                'class': _INPUT,
            }),
            'phone': forms.TextInput(attrs={
                'type': 'tel',
                'class': _INPUT,
                'placeholder': '(11) 99999-9999',
            }),
            'address': forms.Textarea(attrs={
                'rows': 3,
                'class': _TEXTAREA,
                'placeholder': 'Seu endereço completo',
            }),
            'neighborhood': forms.Select(attrs={
                'class': _INPUT,
            }),
            'testimony': forms.Textarea(attrs={
                'rows': 5,
                'class': _TEXTAREA,
                'placeholder': 'Compartilhe sua história de fé e como Deus tem transformado sua vida...',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['neighborhood'].empty_label = 'Selecione seu bairro'
        for field in ('birth_date', 'phone', 'address', 'neighborhood', 'testimony'):
            self.fields[field].required = False


class MemberPasswordChangeForm(forms.Form):
    """Form for member self-service password change."""

    current_password = forms.CharField(
        label='Senha Atual',
        widget=forms.PasswordInput(attrs={
            'class': _INPUT,
            'placeholder': 'Digite sua senha atual',
            'autocomplete': 'current-password',
        }),
    )
    new_password = forms.CharField(
        label='Nova Senha',
        min_length=6,
        widget=forms.PasswordInput(attrs={
            'class': _INPUT,
            'placeholder': 'Digite sua nova senha',
            'autocomplete': 'new-password',
        }),
    )
    confirm_password = forms.CharField(
        label='Confirmar Nova Senha',
        widget=forms.PasswordInput(attrs={
            'class': _INPUT,
            'placeholder': 'Confirme sua nova senha',
            'autocomplete': 'new-password',
        }),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        current = self.cleaned_data.get('current_password')
        if not self.user.check_password(current):
            raise forms.ValidationError('Senha atual incorreta.')
        return current

    def clean(self):
        cleaned_data = super().clean()
        new = cleaned_data.get('new_password')
        confirm = cleaned_data.get('confirm_password')
        if new and confirm and new != confirm:
            self.add_error('confirm_password', 'As senhas não coincidem.')
        return cleaned_data

    def save(self):
        self.user.set_password(self.cleaned_data['new_password'])
        self.user.save()


class MemberForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = [
            'name', 'profile_picture', 'email', 'gender', 'phone', 'address', 'neighborhood', 'birth_date'
        ]
        labels = {
            'name': 'Nome completo',
            'phone': 'Telefone',
            'email': 'Email',
            'address': 'Endereço',
            'neighborhood': 'Bairro',
            'birth_date': 'Data de nascimento',
            'gender': 'Gênero',
            'profile_picture': 'Foto de perfil',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Nome completo'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': '(DDD) 90000-0000'
            }),
            'address': forms.Textarea(attrs={
                'rows': 2,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Rua, número, complemento'
            }),
            'neighborhood': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Selecione o bairro'
            }),
            'birth_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)] flatpickr-date',
                'placeholder': 'Data de nascimento'
            }),
            'gender': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Selecione o gênero'
            })
        }
    
    # Sobrescrever o campo email
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Email'
        }),
        required=True,
    )
    
    password = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Digite sua senha'
        }),
        required=True,
        min_length=6,
        help_text='Mínimo de 6 caracteres'
    )
    
    password_confirm = forms.CharField(
        label='Confirmar senha',
        widget=forms.PasswordInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Confirme sua senha'
        }),
        required=True,
        help_text='Digite a mesma senha novamente'
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Reordenar campos: name, foto, email, password, password_confirm, depois o resto
        field_order = ['name', 'email', 'password', 'password_confirm', 'gender', 'phone', 'address', 'neighborhood', 'birth_date']
        self.order_fields(field_order)
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')
        
        if password and password_confirm:
            if password != password_confirm:
                raise forms.ValidationError('As senhas não coincidem. Por favor, verifique e tente novamente.')
        
        return cleaned_data
