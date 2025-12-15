from django import forms
from ..models.member import Member

class MemberForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = [
            'name', 'profile_picture', 'email', 'gender', 'phone', 'address', 'neighborhood', 'birth_date'
        ]
        labels = {
            'name': 'Nome completo',
            'phone': 'Telefone',
            'email': 'Email',
            'address': 'Endereço',
            'neighborhood': 'Bairro',
            'birth_date': 'Data de nascimento',
            'gender': 'Gênero',
            'profile_picture': 'Foto de perfil',
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
            })
        }
    
    # Sobrescrever o campo email
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Email'
        }),
        required=True,
    )
    
    password = forms.CharField(
        label='Senha',
        widget=forms.PasswordInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Digite sua senha'
        }),
        required=True,
        min_length=6,
        help_text='Mínimo de 6 caracteres'
    )
    
    password_confirm = forms.CharField(
        label='Confirmar senha',
        widget=forms.PasswordInput(attrs={
            'class': 'mt-1 block w-full rounded-xl border border-gray-300 shadow-sm p-2 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]',
            'placeholder': 'Confirme sua senha'
        }),
        required=True,
        help_text='Digite a mesma senha novamente'
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Reordenar campos: name, foto, email, password, password_confirm, depois o resto
        field_order = ['name', 'email', 'password', 'password_confirm', 'gender', 'phone', 'address', 'neighborhood', 'birth_date']
        self.order_fields(field_order)
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')
        
        if password and password_confirm:
            if password != password_confirm:
                raise forms.ValidationError('As senhas não coincidem. Por favor, verifique e tente novamente.')
        
        return cleaned_data
