import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'connect.settings')

app = Celery('connect')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks(['website.external_media'])
