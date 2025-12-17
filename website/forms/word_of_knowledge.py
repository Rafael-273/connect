from django import forms
from website.models import WordOfKnowledge, Healing, Member, Ministry
from datetime import date


class WordOfKnowledgeForm(forms.ModelForm):
    """Formulário para criar/editar palavra de conhecimento"""
    
    class Meta:
        model = WordOfKnowledge
        fields = ['word']
        widgets = {
            'word': forms.Textarea(attrs={
                'class': 'w-full px-4 py-3 border border-gray-200 rounded-xl bg-gray-50 focus:bg-white focus:border-gray-300 focus:outline-none transition-all resize-none',
                'rows': 5,
                'placeholder': 'Descreva a palavra de conhecimento recebida...'
            }),
        }
        labels = {
            'word': 'Palavra de Conhecimento',
        }


class HealingForm(forms.ModelForm):
    """Formulário para registrar cura"""
    
    class Meta:
        model = Healing
        fields = ['healed_person_name', 'body_part', 'condition', 'healing_type', 'description']
        widgets = {
            'healing_type': forms.RadioSelect(attrs={
                'class': 'focus:ring-0',
            }),
            'description': forms.Textarea(attrs={
                'class': 'w-full px-4 py-3 border-2 rounded-xl focus:outline-none transition-all resize-none font-medium',
                'style': 'border-color: rgba(201, 9, 5, 0.2); background: rgba(201, 9, 5, 0.02);',
                'rows': 5,
                'placeholder': 'Descreva o testemunho da cura...'
            }),
            'healed_person_name': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border-2 rounded-xl focus:outline-none transition-all font-medium',
                'style': 'border-color: rgba(201, 9, 5, 0.2); background: rgba(201, 9, 5, 0.02);',
                'placeholder': 'Nome da pessoa curada'
            }),
            'body_part': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border-2 rounded-xl focus:outline-none transition-all font-medium',
                'style': 'border-color: rgba(201, 9, 5, 0.2); background: rgba(201, 9, 5, 0.02);',
                'placeholder': 'Ex: perna, olho, cabeça'
            }),
            'condition': forms.TextInput(attrs={
                'class': 'w-full px-4 py-3 border-2 rounded-xl focus:outline-none transition-all font-medium',
                'style': 'border-color: rgba(201, 9, 5, 0.2); background: rgba(201, 9, 5, 0.02);',
                'placeholder': 'Ex: perna quebrada, cegueira, enxaqueca'
            }),
        }
        labels = {
            'healing_type': 'Tipo de Cura',
            'description': 'Testemunho da Cura',
            'healed_person_name': 'Nome da Pessoa Curada',
            'body_part': 'Parte do Corpo',
            'condition': 'Condição',
        }
