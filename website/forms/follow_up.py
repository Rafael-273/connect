from django import forms
from ..models.follow_up import FollowUp, FollowUpReport
from ..models.member import Member

class FollowUpForm(forms.ModelForm):
    class Meta:
        model = FollowUp
        fields = ['accompanied', 'responsible', 'end_date', 'profile_notes']
        labels = {
            'accompanied': 'Membro Acompanhado',
            'responsible': 'Consolidador Responsável',
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

class FollowUpReportForm(forms.ModelForm):
    class Meta:
        model = FollowUpReport
        fields = ['type', 'status', 'location', 'description', 'prayer_request']
        labels = {
            'type': 'Tipo de Consolidação',
            'status': 'Status do Acompanhamento',
            'location': 'Local do Encontro',
            'description': 'Como foi o encontro?',
            'prayer_request': 'Pedidos de Oração'
        }
        widgets = {
            'type': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'status': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'location': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Ex: Casa do membro, Igreja, Café...'
            }),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Descreva como foi o encontro...'
            }),
            'prayer_request': forms.Textarea(attrs={
                'rows': 3,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Pedidos de oração (opcional)...'
            })
        }
