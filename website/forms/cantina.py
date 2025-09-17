from django import forms
from ..models.canteen import CanteenDebtor
from django.utils import timezone

class CanteenDebtorForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        
        if paid and not paid_date:
            cleaned_data['paid_date'] = timezone.now().date()
        elif not paid:
            cleaned_data['paid_date'] = None
            
        return cleaned_data
