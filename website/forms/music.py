from django import forms
from django.forms import inlineformset_factory
from website.models import Music, ChordSheet


class HiddenCurrentFileInput(forms.ClearableFileInput):
    """ClearableFileInput que não mostra o 'Currently: ...' e 'Change:'"""
    def is_initial(self, value):
        return False


class MusicForm(forms.ModelForm):
    class Meta:
        model = Music
        fields = ['name', 'singer', 'tempo']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Nome da música'
            }),
            'singer': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Cantor ou ministério'
            }),
            'tempo': forms.Select(attrs={
                'class': 'form-input'
            }),
        }


class ChordSheetForm(forms.ModelForm):
    class Meta:
        model = ChordSheet
        fields = ['file', 'tone']
        widgets = {
            'file': HiddenCurrentFileInput(attrs={
                'class': 'form-input',
                'accept': '.pdf'
            }),
            'tone': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ex: Dó, Mi, Sol (opcional)'
            }),
        }


# Formset para criar música com 1 cifra extra
ChordSheetFormSet = inlineformset_factory(
    Music,
    ChordSheet,
    form=ChordSheetForm,
    extra=1,
    can_delete=True
)

# Formset para editar música sem cifra extra
ChordSheetFormSetEdit = inlineformset_factory(
    Music,
    ChordSheet,
    form=ChordSheetForm,
    extra=0,
    can_delete=True
)
