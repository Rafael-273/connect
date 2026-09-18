from django import forms
from website.models import MinistryManual, MediaSubTeam


class MinistryManualForm(forms.ModelForm):
    content = forms.CharField(
        label='Conteúdo', max_length=200000,
        widget=forms.Textarea(attrs={'class': 'form-input manual-source', 'rows': 16, 'data-manual-source': 'true'}),
    )

    class Media:
        css = {'all': ['ministry_manual.css']}
        js = ['ministry_manual.js']

    class Meta:
        model = MinistryManual
        fields = ['title', 'summary', 'sub_team', 'content', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-input'}),
            'summary': forms.Textarea(attrs={'class': 'form-input', 'rows': 2}),
            'sub_team': forms.Select(attrs={'class': 'form-input'}),
        }

    def __init__(self, *args, ministry, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.ministry = ministry
        self.fields['sub_team'].queryset = MediaSubTeam.objects.filter(ministry=ministry).order_by('name')
        self.fields['sub_team'].empty_label = 'Todo o ministério'
        if not self.fields['sub_team'].queryset.exists():
            self.fields['sub_team'].widget = forms.HiddenInput()
