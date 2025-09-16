from django.conf import settings
from django.shortcuts import render
from website.models import Event

def s3_debug_view(request):
    """
    View para debug de problemas com S3.
    Mostra informações sobre configurações do S3 e testa a exibição de imagens.
    """
    context = {
        'debug': settings.DEBUG,
        'use_s3': getattr(settings, 'USE_S3', False),
        'MEDIA_URL': settings.MEDIA_URL,
        'aws_s3_custom_domain': getattr(settings, 'AWS_S3_CUSTOM_DOMAIN', 'Not configured'),
        'aws_storage_bucket_name': getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'Not configured'),
        'aws_querystring_auth': getattr(settings, 'AWS_QUERYSTRING_AUTH', 'Not configured'),
        'events': Event.objects.filter(banner__isnull=False)[:3]  # Pegamos apenas 3 eventos com banner para testar
    }
    return render(request, 'debug/s3_debug.html', context)
