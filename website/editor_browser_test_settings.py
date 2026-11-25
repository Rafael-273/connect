"""Disposable local editor QA: never connects to the application database/S3/queue.

Use explicitly with manage.py test --settings=website.editor_browser_test_settings.
"""
from pathlib import Path
from tempfile import TemporaryDirectory

from connect.settings import *  # noqa: F403

_editor_qa_directory = TemporaryDirectory(prefix='connect-editor-qa-')
_editor_qa_root = Path(_editor_qa_directory.name)
USE_S3 = False
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
MEDIA_ROOT = str(_editor_qa_root / 'media')
EXTERNAL_MEDIA_WORK_ROOT = str(_editor_qa_root / 'work')
STATIC_ROOT = str(_editor_qa_root / 'static')
Path(STATIC_ROOT).mkdir()
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'external_media': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
        'OPTIONS': {'location': MEDIA_ROOT},
    },
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CELERY_TASK_ALWAYS_EAGER = False
EXTERNAL_MEDIA_TAKE_SUGGESTIONS_ENABLED = False
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
