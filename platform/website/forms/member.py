from django import forms
from ..models.member import Member

class MemberForm(forms.ModelForm):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Email'
        }),
        required=False,
    )
    
    class Meta:
        model = Member
        fields = [
            'name', 'profile_picture', 'email', 'gender', 'phone', 'address', 'neighborhood', 'birth_date',
            'marital_status', 'conversion', 'conversion_date',
            'spiritual_maturity', 'ministry', 'personality_type', 'testimony', 'initial_challenges',
            'interests', 'available_days', 'is_available_to_consolidate',
            'is_available_to_disciple'
        ]
        labels = {
            'name': 'Nome completo',
            'phone': 'Telefone',
            'email': 'Email',
            'address': 'Endereço',
            'neighborhood': 'Bairro',
            'birth_date': 'Data de nascimento',
            'gender': 'Gênero',
            'marital_status': 'Estado civil',
            'conversion': 'Convertido?',
            'conversion_date': 'Data da conversão',
            'spiritual_maturity': 'Maturidade espiritual',
            'ministry': 'Ministério',
            'testimony': 'Testemunho',
            'initial_challenges': 'Desafios iniciais',
            'interests': 'Áreas de interesse',
            'available_days': 'Dias disponíveis para consolidar/discipular',
            'is_available_to_consolidate': 'Disponível para consolidar?',
            'is_available_to_disciple': 'Disponível para discipular?',
            'profile_picture': 'Foto de perfil',
            'personality_type': 'Tipo de personalidade'
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Nome completo'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': '(DDD) 90000-0000'
            }),
            'address': forms.Textarea(attrs={
                'rows': 2,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Rua, número, complemento'
            }),
            'neighborhood': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Selecione o bairro'
            }),
            'birth_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)] flatpickr-date',
                'placeholder': 'Data de nascimento'
            }),
            'gender': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Selecione o gênero'
            }),
            'marital_status': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Selecione o estado civil'
            }),
            'conversion': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Foi convertido?'
            }),
            'conversion_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)] flatpickr-date',
                'placeholder': 'Data da conversão'
            }),
            'spiritual_maturity': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Selecione a maturidade espiritual'
            }),
            'ministry': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Selecione o ministério'
            }),
            'testimony': forms.Textarea(attrs={
                'rows': 3,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Testemunho de conversão...'
            }),
            'initial_challenges': forms.Textarea(attrs={
                'rows': 3,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Principais desafios no início da caminhada?'
            }),
            'interests': forms.Textarea(attrs={
                'rows': 2,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Áreas de interesse'
            }),
            'available_days': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Ex: Segunda, Quarta, Domingo...'
            }),
            'is_available_to_consolidate': forms.CheckboxInput(attrs={
                'class': 'form-checkbox h-5 w-5 text-[var(--color-primary)]'
            }),
            'is_available_to_disciple': forms.CheckboxInput(attrs={
                'class': 'form-checkbox h-5 w-5 text-[var(--color-primary)]'
            }),
            'profile_picture': forms.ClearableFileInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'personality_type': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Tipo de personalidade'
            }),
        }
