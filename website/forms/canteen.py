from django import forms

from ..models.canteen import CanteenDebtor


class CanteenDebtorForm(forms.ModelForm):
    class Meta:
        model = CanteenDebtor
        fields = ['name', 'phone', 'purchase_date', 'amount', 'description', 'paid', 'paid_date', 'notes']
        widgets = {
            'purchase_date': forms.DateInput(attrs={'type': 'date'}),
            'paid_date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
