from django import template

register = template.Library()

WEEKDAYS_PT = [
    'Segunda-feira',
    'Terça-feira',
    'Quarta-feira',
    'Quinta-feira',
    'Sexta-feira',
    'Sábado',
    'Domingo'
]


@register.filter(is_safe=True)
def pt_weekday(value):
    """Retorna o nome do dia da semana em português para um objeto date/datetime.

    Ex: date(2025,12,08) -> 'Segunda-feira'
    """
    try:
        # value may be a date or datetime
        weekday = value.weekday()  # 0 = Monday
        return WEEKDAYS_PT[weekday]
    except Exception:
        return ''


@register.filter
def get_item(dictionary, key):
    """Template filter for dict lookup by variable key.

    Usage: {{ my_dict|get_item:some_variable }}
    Returns the value for the given key, or None if not found.
    """
    if dictionary is None:
        return None
    try:
        return dictionary.get(key)
    except (AttributeError, TypeError):
        return None
