from django import forms
from ..models.canteen import CanteenDebtor
from django.utils import timezone

class CanteenDebtorForm(forms.ModelForm):
    """Formulário para o modelo CanteenDebtor"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Adicionar classes aos campos
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:border-[var(--color-primary)]'})
    
    class Meta:
        model = CanteenDebtor
        fields = ['name', 'phone', 'purchase_date', 'amount', 'description', 'paid', 'paid_date', 'notes']
        widgets = {
            'purchase_date': forms.DateInput(attrs={'type': 'date'}),
            'paid_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
    
    def clean(self):
        cleaned_data = super().clean()
        paid = cleaned_data.get('paid')
        paid_date = cleaned_data.get('paid_date')
        phone = cleaned_data.get('phone')
        
        # Processar telefone - remover formatação antes de salvar
        if phone:
            # Remove todos os caracteres não numéricos
            cleaned_phone = ''.join(filter(str.isdigit, phone))
            
            # Remover o código do país 55 se ele estiver no início
            if cleaned_phone.startswith('55') and len(cleaned_phone) > 10:
                cleaned_phone = cleaned_phone[2:]
                
            cleaned_data['phone'] = cleaned_phone
        
        if paid and not paid_date:
            # Se marcado como pago mas sem data, define a data atual
            cleaned_data['paid_date'] = timezone.now().date()
        elif not paid:
            # Se não está pago, limpa a data de pagamento
            cleaned_data['paid_date'] = None
            
        return cleaned_data
