from django import forms
import re
from ..models.evangelism import Evangelized


SPAM_PATTERNS = re.compile(
    r'(https?://|www\.|\.(com|net|org|info|xyz|ru|cn|tk)|casino|poker|viagra|cialis|crypto|bitcoin|loan|prize|winner|click here|free money)',
    re.IGNORECASE
)


class EvangelizedForm(forms.ModelForm):
    # Honeypot — deve ficar em branco
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Evangelized
        fields = ['name', 'gender', 'phone', 'address', 'neighborhood', 'prayer_request', 'wants_peace_house']
        labels = {
            'name': 'Nome completo',
            'gender': 'Gênero',
            'phone': 'Telefone',
            'address': 'Endereço',
            'neighborhood': 'Bairro',
            'prayer_request': 'Pedido de oração',
            'wants_peace_house': 'Deseja Casa de Paz?',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Nome completo'
            }),
            'gender': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
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
                'placeholder': 'Pedido de oração...'
            }),
            'wants_peace_house': forms.CheckboxInput(attrs={
                'class': 'w-4 h-4 accent-[var(--color-primary)]'
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
            self.add_error('name', 'Por favor, informe o nome completo.')

        return cleaned_data
