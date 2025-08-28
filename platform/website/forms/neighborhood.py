from django import forms
from ..models.neighborhood import Neighborhood


class NeighborhoodForm(forms.ModelForm):
    class Meta:
        model = Neighborhood
        fields = ['name', 'parent']
        labels = {
            'name': 'Nome do Bairro',
            'parent': 'Bairro Pai (Opcional)',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ex: Centro, Vila Nova, Jardim América...',
                'maxlength': 100,
            }),
            'parent': forms.Select(attrs={
                'class': 'form-control',
            }),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        super().__init__(*args, **kwargs)
        
        # Set queryset for parent field, excluding current instance to avoid circular references
        if instance and instance.pk:
            self.fields['parent'].queryset = Neighborhood.objects.exclude(pk=instance.pk)
        else:
            self.fields['parent'].queryset = Neighborhood.objects.all()
        
        # Add empty option
        self.fields['parent'].empty_label = "Selecione um bairro pai..."

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if name:
            name = name.strip()
            if len(name) < 2:
                raise forms.ValidationError('Nome deve ter pelo menos 2 caracteres.')
        
        return name

    def clean(self):
        cleaned_data = super().clean()
        parent = cleaned_data.get('parent')
        
        # Ensure we don't create circular references
        if parent and self.instance and self.instance.pk:
            if parent.pk == self.instance.pk:
                raise forms.ValidationError('Um bairro não pode ser pai de si mesmo.')
            
            # Check if parent is actually a child of this neighborhood
            current_parent = parent.parent
            while current_parent:
                if current_parent.pk == self.instance.pk:
                    raise forms.ValidationError('Não é possível criar uma referência circular.')
                current_parent = current_parent.parent
        
        return cleaned_data
