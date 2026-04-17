from django import forms
import re
from ..models.visitor import Visitor


SPAM_PATTERNS = re.compile(
    r'(https?://|www\.|\.(com|net|org|info|xyz|ru|cn|tk)|casino|poker|viagra|cialis|crypto|bitcoin|loan|prize|winner|click here|free money)',
    re.IGNORECASE
)


class VisitorForm(forms.ModelForm):
    # Honeypot — deve ficar em branco
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Visitor
        fields = ['name', 'phone', 'address', 'neighborhood', 'prayer_request', 'wants_home_prayer']
        labels = {
            'name': 'Nome completo',
            'phone': 'Telefone',
            'address': 'Endereço',
            'neighborhood': 'Bairro',
            'prayer_request': 'Pedido de oração',
            'wants_home_prayer': 'Deseja oração na sua casa?',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Nome completo'
            }),
            'phone': forms.TextInput(attrs={
                'id': 'id_telephone',
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': '(DDD) 90000-0000'
            }),
            'address': forms.Textarea(attrs={
                'rows': 2,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Rua, número, complemento'
            }),
            'neighborhood': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'prayer_request': forms.Textarea(attrs={
                'rows': 3,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Seu pedido de oração...'
            }),
        }

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get('website'):
            raise forms.ValidationError('Submissão inválida.')

        for field in ('name', 'address', 'prayer_request'):
            value = cleaned_data.get(field, '') or ''
            if SPAM_PATTERNS.search(value):
                raise forms.ValidationError('Conteúdo inválido detectado. Revise as informações.')

        name = cleaned_data.get('name', '')
        if name and len(name.strip()) < 3:
            self.add_error('name', 'Por favor, informe seu nome completo.')

        return cleaned_data


class VisitorAdminForm(forms.ModelForm):
    """Formulário administrativo para criar/editar visitantes no painel.
    
    Nota: visit_date é auto_now_add=True (não-editável), por isso é
    tratado separadamente no view e não faz parte deste formulário.
    """

    class Meta:
        model = Visitor
        fields = [
            'name', 'email', 'phone', 'birth_date', 'gender', 'address', 'neighborhood',
            'decision_for_jesus', 'wants_home_prayer',
            'conversion', 'prayer_request', 'profile_notes',
        ]
        labels = {
            'name':               'Nome Completo',
            'email':              'Email',
            'phone':              'Telefone',
            'birth_date':         'Data de Nascimento',
            'gender':             'Gênero',
            'address':            'Endereço Completo',
            'neighborhood':       'Bairro',
            'decision_for_jesus': 'Fez decisão por Jesus?',
            'wants_home_prayer':  'Deseja oração na sua casa?',
            'conversion':         'Tipo de Conversão',
            'prayer_request':     'Pedido de Oração',
            'profile_notes':      'Observações/Anotações',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Nome completo do visitante',
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-input',
                'placeholder': 'email@exemplo.com',
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '(11) 99999-9999',
            }),
            'birth_date': forms.DateInput(attrs={
                'class': 'form-input',
                'type': 'date',
            }),
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'address': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Rua, número, bairro, cidade',
            }),
            'neighborhood':       forms.Select(attrs={'class': 'form-select'}),
            'decision_for_jesus': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'wants_home_prayer':  forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'conversion': forms.Select(attrs={'class': 'form-select'}),
            'prayer_request': forms.Textarea(attrs={
                'rows': 4,
                'class': 'form-textarea',
                'placeholder': 'Descreva o pedido de oração do visitante...',
            }),
            'profile_notes': forms.Textarea(attrs={
                'rows': 4,
                'class': 'form-textarea',
                'placeholder': 'Adicione observações sobre o visitante...',
            }),
        }

    def clean_email(self):
        """Garante unicidade de email entre visitantes."""
        email = self.cleaned_data.get('email') or None
        if email:
            qs = Visitor.objects.filter(email=email)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    f'Já existe um visitante com o email "{email}". Por favor, use um email diferente.'
                )
        return email

    def clean(self):
        cleaned_data = super().clean()
        # Se fez decisão por Jesus mas não informou tipo, assume novo convertido
        if cleaned_data.get('decision_for_jesus') and not cleaned_data.get('conversion'):
            cleaned_data['conversion'] = 'new_convert'
        return cleaned_data

    def save(self, commit=True, visit_date=None):
        instance = super().save(commit=False)
        if commit:
            instance.save()
            # auto_now_add=True substitui visit_date no INSERT;
            # usamos UPDATE para persistir o valor informado.
            if visit_date:
                Visitor.objects.filter(pk=instance.pk).update(visit_date=visit_date)
                instance.visit_date = visit_date
        return instance
