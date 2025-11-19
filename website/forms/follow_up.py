from django import forms
from ..models.follow_up import FollowUp, FollowUpReport, FollowUpTemplate
from ..models.member import Member

class FollowUpForm(forms.ModelForm):
    class Meta:
        model = FollowUp
        fields = ['accompanied', 'responsible', 'template', 'end_date', 'profile_notes']
        labels = {
            'accompanied': 'Membro Acompanhado',
            'responsible': 'Consolidador Responsável',
            'template': 'Template de Consolidação',
            'end_date': 'Data de Finalização',
            'profile_notes': 'Observações do Perfil'
        }
        widgets = {
            'accompanied': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'responsible': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'template': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'onchange': 'updateTemplateInfo(this.value)'
            }),
            'end_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'profile_notes': forms.Textarea(attrs={
                'rows': 4,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Observações sobre o perfil do membro...'
            })
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filtrar apenas membros ativos
        self.fields['accompanied'].queryset = Member.objects.filter(is_active=True).order_by('name')
        self.fields['responsible'].queryset = Member.objects.filter(
            is_active=True,
            is_available_to_consolidate=True
        ).order_by('name')
        
        # Configurar templates disponíveis
        self.fields['template'].queryset = FollowUpTemplate.objects.all().order_by('name')
        self.fields['template'].empty_label = "Selecione um template..."

class FollowUpReportForm(forms.ModelForm):
    class Meta:
        model = FollowUpReport
        fields = [
            'week',
            'type', 
            'status', 
            'description', 
            'attended_service',
            'reading_bible',
            'praying_regularly',
            'is_receptive',
            'responding_messages',
            'attending_classes',
            'volunteering',
            'met_one_on_one',
            'met_in_group',
            'building_relationships',
            'lifestyle_changes',
            'overcoming_struggles',
            'prayer_request'
        ]
        labels = {
            'week': 'Semana',
            'type': 'Tipo de Consolidação',
            'status': 'Como está o progresso?',
            'description': 'Como foi a semana?',
            'attended_service': 'Participou do culto?',
            'reading_bible': 'Está lendo a Bíblia?',
            'praying_regularly': 'Está orando regularmente?',
            'is_receptive': 'Está receptivo?',
            'responding_messages': 'Respondeu mensagens?',
            'attending_classes': 'Participou das aulas?',
            'volunteering': 'Participou como voluntário?',
            'met_one_on_one': 'Saiu sozinho com o consolidador?',
            'met_in_group': 'Saiu em grupo?',
            'building_relationships': 'Está construindo relacionamentos saudáveis?',
            'lifestyle_changes': 'Houve mudanças no estilo de vida?',
            'overcoming_struggles': 'Está superando lutas/vícios?',
            'prayer_request': 'Pedidos de Oração'
        }
        widgets = {
            'week': forms.NumberInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'min': '1'
            }),
            'type': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'status': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Descreva como foi este período de acompanhamento...'
            }),
            'attended_service': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'reading_bible': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'praying_regularly': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'is_receptive': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'responding_messages': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'attending_classes': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'volunteering': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'met_one_on_one': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'met_in_group': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'building_relationships': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'lifestyle_changes': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'overcoming_struggles': forms.CheckboxInput(attrs={
                'class': 'h-4 w-4 text-[var(--color-primary)] focus:ring-[var(--color-primary)] border-gray-300 rounded'
            }),
            'prayer_request': forms.Textarea(attrs={
                'rows': 3,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Pedidos de oração (opcional)...'
            })
        }
    
    def __init__(self, *args, **kwargs):
        followup = kwargs.pop('followup', None)
        super().__init__(*args, **kwargs)
        
        # Pré-preencher a semana atual e tipo se estiver criando novo relatório
        if followup and not self.instance.pk:
            self.fields['week'].initial = followup.current_week
            self.fields['type'].initial = 'consolidation'  # Sempre consolidação por padrão
