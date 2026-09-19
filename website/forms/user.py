from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()

_INPUT = 'form-input'
_SELECT = 'form-select'
_CHECKBOX = (
    'form-checkbox h-4 w-4 sm:h-5 sm:w-5 text-[var(--color-primary)] '
    'rounded border-gray-300 focus:ring-[var(--color-primary)]'
)


class UserAdminForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'user_type', 'is_active']
        widgets = {
            'email': forms.EmailInput(attrs={'class': _INPUT}),
            'first_name': forms.TextInput(attrs={'class': _INPUT}),
            'last_name': forms.TextInput(attrs={'class': _INPUT}),
            'user_type': forms.Select(attrs={'class': _SELECT}),
            'is_active': forms.CheckboxInput(attrs={'class': _CHECKBOX}),
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = user.user_type != 'member'
        if commit:
            user.save()
        return user
