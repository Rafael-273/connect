from django import forms
from django.core.exceptions import ValidationError
from ..models.house_of_peace import HouseOfPeace
from ..models.neighborhood import Neighborhood


class HouseOfPeacePublicForm(forms.ModelForm):
    """Form for public House of Peace requests."""

    prayer_types = forms.MultipleChoiceField(
        choices=HouseOfPeace.PRAYER_TYPE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        label='Pedidos de Oração',
        help_text='Selecione os motivos (pode marcar vários)',
    )

    class Meta:
        model = HouseOfPeace
        fields = [
            'family_name',
            'phone',
            'address',
            'neighborhood',
            'family_size',
            'prayer_types',
            'prayer_description',
        ]
        labels = {
            'family_name': 'Seu nome',
            'phone': 'Telefone / WhatsApp',
            'address': 'Endereço completo',
            'neighborhood': 'Bairro',
            'family_size': 'Quantas pessoas moram na casa?',
            'prayer_description': 'Descreva com mais detalhes (opcional)',
        }
        help_texts = {
            'family_size': 'Ex: 4',
            'prayer_description': 'Conte-nos mais sobre sua situação e o que deseja orar...',
        }
        widgets = {
            'family_name': forms.TextInput(attrs={
                'placeholder': 'Seu nome completo',
                'class': 'form-input',
            }),
            'phone': forms.TextInput(attrs={
                'type': 'tel',
                'placeholder': '(00) 00000-0000',
                'class': 'form-input',
            }),
            'address': forms.TextInput(attrs={
                'placeholder': 'Rua, número, complemento',
                'class': 'form-input',
            }),
            'neighborhood': forms.Select(attrs={
                'class': 'select-input',
            }),
            'family_size': forms.NumberInput(attrs={
                'min': '1',
                'max': '30',
                'placeholder': 'Ex: 4',
                'class': 'form-input',
            }),
            'prayer_description': forms.Textarea(attrs={
                'rows': '4',
                'style': 'resize: none;',
                'placeholder': 'Conte-nos mais sobre sua situação e o que deseja orar...',
                'class': 'form-input',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make neighborhood sorted by name
        self.fields['neighborhood'].queryset = Neighborhood.objects.all().order_by('name')
        self.fields['neighborhood'].empty_label = 'Selecione seu bairro...'

    def clean_phone(self):
        """Clean and validate phone number."""
        phone = self.cleaned_data.get('phone', '').strip()
        if not phone:
            raise ValidationError('Telefone é obrigatório.')
        
        # Remove formatting for validation
        clean_phone = ''.join(filter(str.isdigit, phone))
        if len(clean_phone) < 10:
            raise ValidationError('Telefone deve ter pelo menos 10 dígitos.')
        
        return phone

    def clean_prayer_types(self):
        """Ensure at least one prayer type is selected."""
        prayer_types = self.cleaned_data.get('prayer_types')
        if not prayer_types:
            raise ValidationError('Selecione pelo menos um motivo de oração.')
        return prayer_types

    def clean(self):
        """Additional validation."""
        cleaned_data = super().clean()
        family_size = cleaned_data.get('family_size')
        
        if family_size and (family_size < 1 or family_size > 30):
            self.add_error('family_size', 'O número de pessoas deve estar entre 1 e 30.')
        
        return cleaned_data

    def save(self, commit=True):
        """Save with prayer_types as comma-separated string."""
        instance = super().save(commit=False)
        prayer_types = self.cleaned_data.get('prayer_types', [])
        instance.prayer_types = ','.join(prayer_types) if prayer_types else ''
        if commit:
            instance.save()
        return instance
