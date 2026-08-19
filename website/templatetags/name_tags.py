from django import template

register = template.Library()


@register.filter
def first_two_names(value):
    """Return only the first and second words from a full name."""
    if not value:
        return ''

    parts = str(value).split()
    normalized = [part.lower().capitalize() for part in parts[:2]]
    return ' '.join(normalized)
