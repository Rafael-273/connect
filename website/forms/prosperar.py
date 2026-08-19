from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from ..models.prosperar import ProsperarCompany
from ..models.neighborhood import Neighborhood
from ..models.user import User


class ProsperarCompanyRegistrationForm(forms.ModelForm):
    email = forms.EmailField(
        label='Email de acesso',
        widget=forms.EmailInput(attrs={
            'placeholder': 'empresa@exemplo.com',
            'class': 'form-input',
        }),
    )
    password = forms.CharField(
        label='Senha',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Crie uma senha segura',
            'class': 'form-input',
        }),
        help_text='Essa senha sera usada para acessar a area da empresa no Prosperar.',
    )
    password_confirm = forms.CharField(
        label='Confirmar senha',
        strip=False,
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Digite a senha novamente',
            'class': 'form-input',
        }),
    )

    class Meta:
        model = ProsperarCompany
        fields = [
            'company_name',
            'business_sector',
            'neighborhood',
            'logo',
            'address',
            'phone',
            'description',
            'instagram_url',
        ]
        labels = {
            'company_name': 'Nome da empresa',
            'business_sector': 'Qual o ramo da atividade?',
            'neighborhood': 'Bairro',
            'logo': 'Logo da empresa',
            'address': 'Endereco',
            'phone': 'Telefone / WhatsApp',
            'description': 'Descricao do negocio',
            'instagram_url': 'Link do Instagram',
        }
        help_texts = {
            'logo': 'Opcional. JPG, PNG ou WebP com ate 5MB.',
            'description': 'Opcional. Conte em poucas linhas o que sua empresa faz.',
            'instagram_url': 'Opcional. Cole o link completo do perfil. Ex: https://instagram.com/minhaempresa',
        }
        widgets = {
            'company_name': forms.TextInput(attrs={
                'placeholder': '',
                'class': 'form-input',
            }),
            'business_sector': forms.Select(attrs={
                'class': 'select-input',
            }),
            'neighborhood': forms.Select(attrs={
                'class': 'select-input',
            }),
            'address': forms.Textarea(attrs={
                'rows': 2,
                'placeholder': '',
                'class': 'form-input',
                'style': 'resize: none;',
            }),
            'phone': forms.TextInput(attrs={
                'type': 'tel',
                'placeholder': 'Ex: (21) 99999-9999',
                'class': 'form-input',
            }),
            'description': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Ex: Trabalhamos com bolos, doces e salgados para festas e encomendas.',
                'class': 'form-input',
                'style': 'resize: none;',
            }),
            'instagram_url': forms.URLInput(attrs={
                'placeholder': 'Ex: https://instagram.com/minhaempresa',
                'class': 'form-input',
            }),
            'logo': forms.ClearableFileInput(attrs={
                'accept': 'image/png,image/jpeg,image/webp',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.existing_user = None
        self.used_existing_member_account = False
        self.fields['business_sector'].choices = [('', 'Selecione o ramo')] + list(ProsperarCompany.BUSINESS_SECTOR_CHOICES)
        self.fields['neighborhood'].queryset = Neighborhood.objects.all().order_by('name')
        self.fields['neighborhood'].empty_label = 'Selecione seu bairro'

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        existing_user = (
            User.objects
            .filter(email__iexact=email)
            .select_related('member')
            .first()
        )

        if not existing_user:
            self.existing_user = None
            return email

        if ProsperarCompany.objects.filter(user=existing_user).exists():
            raise ValidationError('Ja existe uma empresa cadastrada com este email.')

        if hasattr(existing_user, 'member'):
            self.existing_user = existing_user
            self.used_existing_member_account = True
            return email

        raise ValidationError('Ja existe uma conta com este email. Use outro email ou acesse com uma conta de membro da Filadelfia.')

        return email

    def clean_logo(self):
        logo = self.cleaned_data.get('logo')
        if not logo:
            return logo

        max_size = 5 * 1024 * 1024
        allowed_types = {'image/jpeg', 'image/png', 'image/webp'}

        if getattr(logo, 'size', 0) > max_size:
            raise ValidationError('A logo deve ter no maximo 5MB.')

        if getattr(logo, 'content_type', '') not in allowed_types:
            raise ValidationError('Envie a logo em JPG, PNG ou WebP.')

        return logo

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if not phone:
            return phone

        clean_phone = ''.join(filter(str.isdigit, phone))
        if len(clean_phone) < 10:
            raise ValidationError('Telefone deve ter pelo menos 10 digitos.')

        return phone

    def clean_instagram_url(self):
        instagram_url = self.cleaned_data.get('instagram_url', '').strip()
        return instagram_url

    def clean_neighborhood(self):
        neighborhood = self.cleaned_data.get('neighborhood')
        if neighborhood is None:
            return neighborhood
        return neighborhood

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')

        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'As senhas nao conferem.')

        if password and not self.used_existing_member_account:
            temp_user = User(email=cleaned_data.get('email', ''))
            try:
                validate_password(password, user=temp_user)
            except ValidationError as exc:
                self.add_error('password', exc)

        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        company = super().save(commit=False)
        user = self.existing_user

        if user is None:
            user = User.objects.create_user(
                email=self.cleaned_data['email'],
                password=self.cleaned_data['password'],
                user_type='prosperar',
                is_staff=False,
            )

        company.user = user
        company.member = getattr(user, 'member', None)

        if commit:
            company.save()

        return company


class ProsperarCompanyProfileForm(forms.ModelForm):
    class Meta:
        model = ProsperarCompany
        fields = [
            'company_name',
            'business_sector',
            'logo',
            'neighborhood',
            'address',
            'phone',
            'instagram_url',
            'description',
        ]
        labels = {
            'company_name': 'Nome da empresa',
            'business_sector': 'Ramo de atividade',
            'logo': 'Logo da empresa',
            'neighborhood': 'Bairro',
            'address': 'Endereco',
            'phone': 'Telefone / WhatsApp',
            'instagram_url': 'Link do Instagram',
            'description': 'Descricao do negocio',
        }
        widgets = {
            'company_name': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Nome da sua empresa',
            }),
            'business_sector': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            }),
            'logo': forms.ClearableFileInput(attrs={
                'accept': 'image/png,image/jpeg,image/webp',
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 bg-white p-3 text-gray-700 shadow-sm focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            }),
            'neighborhood': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            }),
            'address': forms.Textarea(attrs={
                'rows': 2,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Rua, numero e complemento',
            }),
            'phone': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': '(21) 99999-9999',
            }),
            'instagram_url': forms.URLInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'https://instagram.com/suaempresa',
            }),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Conte um pouco sobre sua empresa, seus produtos e servicos.',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['business_sector'].choices = [('', 'Selecione o ramo')] + list(ProsperarCompany.BUSINESS_SECTOR_CHOICES)
        self.fields['neighborhood'].queryset = Neighborhood.objects.all().order_by('name')
        self.fields['neighborhood'].empty_label = 'Selecione seu bairro'
        self.fields['logo'].required = False
        self.fields['phone'].required = False
        self.fields['instagram_url'].required = False
        self.fields['description'].required = False
        self.fields['neighborhood'].required = False

    def clean_logo(self):
        logo = self.cleaned_data.get('logo')
        if not logo:
            return logo

        max_size = 5 * 1024 * 1024
        allowed_types = {'image/jpeg', 'image/png', 'image/webp'}

        if getattr(logo, 'size', 0) > max_size:
            raise ValidationError('A logo deve ter no maximo 5MB.')

        if getattr(logo, 'content_type', '') not in allowed_types:
            raise ValidationError('Envie a logo em JPG, PNG ou WebP.')

        return logo


class ProsperarPasswordChangeForm(forms.Form):
    current_password = forms.CharField(
        label='Senha atual',
        widget=forms.PasswordInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Digite sua senha atual',
            'autocomplete': 'current-password',
        }),
    )
    new_password = forms.CharField(
        label='Nova senha',
        min_length=6,
        widget=forms.PasswordInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Digite sua nova senha',
            'autocomplete': 'new-password',
        }),
    )
    confirm_password = forms.CharField(
        label='Confirmar nova senha',
        widget=forms.PasswordInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-3 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
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
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')

        if new_password:
            validate_password(new_password, user=self.user)

        if new_password and confirm_password and new_password != confirm_password:
            self.add_error('confirm_password', 'As senhas nao coincidem.')

        return cleaned_data

    def save(self):
        self.user.set_password(self.cleaned_data['new_password'])
        self.user.save()
