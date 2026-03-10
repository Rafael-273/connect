from django import forms
from ..models.prayer_request import PrayerRequest


class PrayerRequestForm(forms.ModelForm):
    # Honeypot field — must be left blank by real users
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = PrayerRequest
        fields = ['name', 'content']
        labels = {
            'name': 'Nome (opcional)',
            'content': 'Pedido de oração',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Seu nome (opcional)',
            }),
            'content': forms.Textarea(attrs={
                'rows': 5,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Escreva seu pedido de oração...',
            }),
        }

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('website'):
            raise forms.ValidationError('Submissão inválida.')
        return cleaned_data
