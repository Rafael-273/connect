from django import forms
from website.models import WordOfKnowledge, Healing, MinistrationSchedule, Member, Ministry
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


class MinistrationScheduleForm(forms.ModelForm):
    """Formulário para criar/editar escala de ministração"""
    
    class Meta:
        model = MinistrationSchedule
        fields = ['week_start_date', 'members', 'leaders', 'notes']
        widgets = {
            'week_start_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500'
            }),
            'members': forms.CheckboxSelectMultiple(),
            'leaders': forms.CheckboxSelectMultiple(),
            'notes': forms.Textarea(attrs={
                'rows': 3,
                'class': 'mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-red-500 focus:ring-red-500',
                'placeholder': 'Observações sobre a escala...'
            }),
        }
        labels = {
            'week_start_date': 'Início da Semana (Segunda-feira)',
            'members': 'Ministradores Escalados',
            'leaders': 'Líder(es) da Equipe',
            'notes': 'Observações',
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filtrar apenas membros do ministério de ministração
        try:
            ministracao_ministry = Ministry.objects.filter(name__icontains='ministração').first()
            if ministracao_ministry:
                members_queryset = Member.objects.filter(
                    ministry=ministracao_ministry,
                    is_active=True
                ).order_by('name')
                self.fields['members'].queryset = members_queryset
                self.fields['leaders'].queryset = members_queryset
            else:
                # Se não existir ministério de ministração, não mostrar membros
                self.fields['members'].queryset = Member.objects.none()
                self.fields['leaders'].queryset = Member.objects.none()
        except Exception:
            self.fields['members'].queryset = Member.objects.none()
            self.fields['leaders'].queryset = Member.objects.none()
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        # Sempre definir is_active como True ao criar/salvar
        instance.is_active = True
        if commit:
            instance.save()
            self.save_m2m()
        return instance
