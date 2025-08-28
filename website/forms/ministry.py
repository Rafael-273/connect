from django import forms
from ..models.ministry import Ministry


class MinistryForm(forms.ModelForm):
    class Meta:
        model = Ministry
        fields = ['name', 'description']
        labels = {
            'name': 'Nome do Ministério',
            'description': 'Descrição',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ex: Ministério de Louvor, Ministério Infantil...',
                'maxlength': 100,
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 4,
                'placeholder': 'Descreva as atividades e propósito do ministério...',
            }),
        }

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name:
            name = name.strip()
            if len(name) < 2:
                raise forms.ValidationError('Nome deve ter pelo menos 2 caracteres.')
            
            # Check for duplicate names (excluding current instance if editing)
            qs = Ministry.objects.filter(name__iexact=name)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            
            if qs.exists():
                raise forms.ValidationError('Já existe um ministério com este nome.')
        
        return name
