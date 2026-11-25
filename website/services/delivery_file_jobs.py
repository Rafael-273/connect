"""Legacy Editor files are fingerprinted in Celery, never inside a web request."""
import logging

from celery import shared_task
from django.db import transaction

logger = logging.getLogger(__name__)


@shared_task(name='media_deliveries.index_editor_file')
def index_editor_file(source_id):
    from website.models import MediaAsset, ContentFile
    from website.services.content_deliveries import index_render_file
    try:
        with transaction.atomic():
            source = MediaAsset.objects.select_for_update(of=('self',)).select_related('job__created_by', 'content_file').get(pk=source_id)
            asset = index_render_file(source)
            if asset:
                asset.metadata.pop('hash_requested_at', None)
                asset.metadata.pop('hash_error', None)
                asset.save(update_fields=['metadata'])
    except Exception:
        logger.exception('delivery_editor_hash_failed source=%s', source_id)
        source = MediaAsset.objects.filter(pk=source_id).first()
        if source and source.content_file_id:
            asset = ContentFile.objects.get(pk=source.content_file_id)
            asset.metadata['hash_error'] = True
            asset.metadata.pop('hash_requested_at', None)
            asset.save(update_fields=['metadata'])


def enqueue_editor_index(source_id):
    try:
        index_editor_file.delay(source_id)
    except Exception:
        logger.exception('delivery_editor_hash_enqueue_failed source=%s', source_id)
        from website.models import MediaAsset
        source = MediaAsset.objects.select_related('content_file').get(pk=source_id)
        if source.content_file:
            asset = source.content_file
            asset.metadata['hash_error'] = True
            asset.metadata.pop('hash_requested_at', None)
            asset.save(update_fields=['metadata'])
