from django import forms
from ..models.follow_up import FollowUpTemplate, FollowUpTemplateStep

class FollowUpTemplateForm(forms.ModelForm):
    class Meta:
        model = FollowUpTemplate
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Nome do template (ex: Consolidação Básica - 4 Semanas)'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'rows': 4,
                'placeholder': 'Descreva o propósito e características deste template...'
            })
        }

class FollowUpTemplateStepForm(forms.ModelForm):
    class Meta:
        model = FollowUpTemplateStep
        fields = ['week', 'title', 'description']
        widgets = {
            'week': forms.NumberInput(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'min': 1,
                'max': 52
            }),
            'title': forms.TextInput(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Título da etapa (ex: Primeiro Contato, Ensino Básico...)'
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'rows': 3,
                'placeholder': 'Descrição opcional - adicione mais detalhes se necessário...'
            })
        }

# Formset para gerenciar múltiplas etapas
FollowUpTemplateStepFormSet = forms.inlineformset_factory(
    FollowUpTemplate, 
    FollowUpTemplateStep,
    form=FollowUpTemplateStepForm,
    extra=0,  # Sem formulários extras por padrão
    can_delete=True,  # Permitir exclusão
    min_num=0,  # Permitir 0 etapas inicialmente
    validate_min=False
)

# Formset específico para criação (com uma etapa vazia)
FollowUpTemplateStepFormSetForCreate = forms.inlineformset_factory(
    FollowUpTemplate, 
    FollowUpTemplateStep,
    form=FollowUpTemplateStepForm,
    extra=1,  # Uma etapa vazia para começar
    can_delete=True,
    min_num=1,  # Mínimo de 1 etapa
    validate_min=True
)