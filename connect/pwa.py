"""Serve the service worker at the root so it can control application pages."""
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_safe


@require_safe
def service_worker(request):
    source = Path(settings.BASE_DIR) / 'static' / 'sw.js'
    response = HttpResponse(source.read_bytes(), content_type='application/javascript')
    response['Cache-Control'] = 'no-cache'
    response['Service-Worker-Allowed'] = '/'
    return response

