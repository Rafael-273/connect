from django import forms


class RelativeDaysWidget(forms.MultiWidget):
    def __init__(self, attrs=None):
        super().__init__([
            forms.NumberInput(attrs={'min': 0, 'placeholder': 'Dias', 'aria-label': 'Quantidade de dias'}),
            forms.Select(choices=[('before', 'Dias antes'), ('on', 'No dia'), ('after', 'Dias depois')], attrs={'aria-label': 'Em relação ao evento'}),
        ], attrs=attrs or {'class': 'form-input'})

    def decompress(self, value):
        if value is None or value == '':
            return [None, 'before']
        value = int(value)
        return [abs(value), 'before' if value < 0 else 'after' if value > 0 else 'on']


class RelativeDaysField(forms.MultiValueField):
    widget = RelativeDaysWidget

    def __init__(self, **kwargs):
        super().__init__(fields=[forms.IntegerField(min_value=0, max_value=2147483647, required=False), forms.ChoiceField(choices=[('before', 'Antes'), ('on', 'No dia'), ('after', 'Depois')])], require_all_fields=False, required=False, **kwargs)

    def compress(self, values):
        if not values:
            return None
        days, relation = values
        if relation == 'on':
            return 0
        if days is None:
            return None
        return -days if relation == 'before' else days
