from django import forms
from ..models.visitor import Visitor


class VisitorForm(forms.ModelForm):
    class Meta:
        model = Visitor
        fields = ['name', 'email', 'phone', 'address', 'neighborhood', 'prayer_request']
        labels = {
            'name': 'Nome completo',
            'email': 'Email',
            'phone': 'Telefone',
            'address': 'Endereço',
            'neighborhood': 'Bairro',
            'prayer_request': 'Pedido de oração',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Nome completo'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Seu email'
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


class VisitorAdminForm(forms.ModelForm):
    """Formulário administrativo para visitantes com campos de conversão"""
    
    class Meta:
        model = Visitor
        fields = [
            'name', 'email', 'phone', 'gender', 'address', 'neighborhood', 
            'decision_for_jesus', 'conversion', 
            'prayer_request', 'profile_notes'
        ]
        labels = {
            'name': 'Nome Completo',
            'email': 'Email',
            'phone': 'Telefone',
            'gender': 'Gênero',
            'address': 'Endereço Completo',
            'neighborhood': 'Bairro',
            'visit_date': 'Data da Visita (automática)',
            'decision_for_jesus': 'Fez decisão por Jesus?',
            'conversion': 'Tipo de Conversão',
            'prayer_request': 'Pedido de Oração',
            'profile_notes': 'Observações/Anotações',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Nome completo do visitante'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-input',
                'placeholder': 'email@exemplo.com'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '(11) 99999-9999'
            }),
            'gender': forms.Select(attrs={
                'class': 'form-select'
            }),
            'address': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Rua, número, bairro, cidade'
            }),
            'neighborhood': forms.Select(attrs={
                'class': 'form-select'
            }),
            'decision_for_jesus': forms.CheckboxInput(attrs={
                'class': 'checkbox-input'
            }),
            'conversion': forms.Select(attrs={
                'class': 'form-select'
            }),
            'prayer_request': forms.Textarea(attrs={
                'rows': 4,
                'class': 'form-textarea',
                'placeholder': 'Descreva o pedido de oração do visitante...'
            }),
            'profile_notes': forms.Textarea(attrs={
                'rows': 4,
                'class': 'form-textarea',
                'placeholder': 'Adicione observações sobre o visitante...'
            }),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tornar campos obrigatórios
        self.fields['name'].required = True
        
        # Adicionar classes CSS aos campos
        for field_name, field in self.fields.items():
            if field.widget.__class__.__name__ == 'TextInput':
                field.widget.attrs['class'] = 'form-input'
            elif field.widget.__class__.__name__ == 'EmailInput':
                field.widget.attrs['class'] = 'form-input'
            elif field.widget.__class__.__name__ == 'Select':
                field.widget.attrs['class'] = 'form-select'
            elif field.widget.__class__.__name__ == 'Textarea':
                field.widget.attrs['class'] = 'form-textarea'
            elif field.widget.__class__.__name__ == 'CheckboxInput':
                field.widget.attrs['class'] = 'checkbox-input'

    def clean(self):
        cleaned_data = super().clean()
        decision_for_jesus = cleaned_data.get('decision_for_jesus')
        conversion = cleaned_data.get('conversion')
        
        # Validação: Se tem decisão por Jesus ou conversão, deve ser consistente
        if decision_for_jesus and not conversion:
            # Se fez decisão mas não tem tipo, assume novo convertido
            cleaned_data['conversion'] = 'new_convert'
        
        return cleaned_data
