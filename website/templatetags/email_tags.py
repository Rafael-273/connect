import base64
import mimetypes
import os

from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def static_base64(path):
    """
    Reads a file by absolute path (or relative to BASE_DIR) and returns a base64 data URI.
    Usage: {% static_base64 '/full/path/to/image.png' %}
    """
    if os.path.isfile(path):
        file_path = path
    else:
        file_path = os.path.join(settings.BASE_DIR, path.lstrip('/'))
        if not os.path.isfile(file_path):
            file_path = None

    if not file_path:
        return ''

    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = 'image/png'

    with open(file_path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('utf-8')

    return f'data:{mime_type};base64,{encoded}'
