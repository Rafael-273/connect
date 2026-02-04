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

MONTHS_PT = [
    'Janeiro',
    'Fevereiro',
    'Março',
    'Abril',
    'Maio',
    'Junho',
    'Julho',
    'Agosto',
    'Setembro',
    'Outubro',
    'Novembro',
    'Dezembro'
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


@register.filter(is_safe=True)
def pt_month(value):
    """Retorna o nome do mês em português para um objeto date/datetime.

    Ex: date(2025,12,08) -> 'Dezembro'
    """
    try:
        # value may be a date or datetime
        month = value.month  # 1-12
        return MONTHS_PT[month - 1]
    except Exception:
        return ''
