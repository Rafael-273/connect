from django import forms
from ..models.evangelism import Evangelized


class EvangelizedForm(forms.ModelForm):
    class Meta:
        model = Evangelized
        fields = ['name', 'gender', 'phone', 'address', 'neighborhood', 'evangelized_by', 'conversion', 'prayer_request', 'profile_notes']
        labels = {
            'name': 'Nome completo',
            'gender': 'Gênero',
            'phone': 'Telefone',
            'address': 'Endereço',
            'neighborhood': 'Bairro',
            'evangelized_by': 'Evangelizado por',
            'conversion': 'Tipo de conversão',
            'prayer_request': 'Pedido de oração',
            'profile_notes': 'Observações',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
                'placeholder': 'Nome completo'
            }),
            'gender': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
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
            'evangelized_by': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'conversion': forms.Select(attrs={
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 text-gray-700 bg-white focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]'
            }),
            'prayer_request': forms.Textarea(attrs={
                'rows': 3,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Pedido de oração...'
            }),
            'profile_notes': forms.Textarea(attrs={
                'rows': 3,
                'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 resize-none focus:outline-none focus:border-[var(--color-primary)]',
                'placeholder': 'Observações adicionais...'
            }),
        }
