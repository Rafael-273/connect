"""Manual delivery files stay private even with the local development backend."""
from django.conf import settings
from django.core.files.storage import FileSystemStorage, storages


def get_content_file_storage():
    external = storages['external_media']
    if hasattr(external, 'bucket_name'):
        return external
    return FileSystemStorage(location=getattr(settings, 'CONTENT_FILES_ROOT', settings.BASE_DIR / '.private_content_files'))
