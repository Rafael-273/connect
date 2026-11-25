"""Expire only generated render checkpoints. Dry-run unless --apply is supplied."""
from datetime import timedelta
import re
from django.conf import settings
from django.core.files.storage import storages
from django.core.management.base import BaseCommand
from django.utils import timezone
from website.external_media.render_cache import PREFIX


class Command(BaseCommand):
    help = 'Lista checkpoints de render expirados; --apply remove apenas o cache gerado.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        storage = storages['external_media']
        cutoff = timezone.now() - timedelta(seconds=max(0, int(getattr(settings, 'EXTERNAL_MEDIA_RENDER_CACHE_TTL_SECONDS', 604800))))
        def walk(prefix):
            directories, files = storage.listdir(prefix)
            for name in files:
                yield f'{prefix}/{name}'
            for name in directories:
                if re.fullmatch(r'[0-9a-f]{64}', name):
                    yield from walk(f'{prefix}/{name}')
        count = 0
        try:
            for name in walk(PREFIX):
                if storage.get_modified_time(name) < cutoff:
                    count += 1
                    self.stdout.write(name)
                    if options['apply']:
                        storage.delete(name)
        except FileNotFoundError:
            pass
        self.stdout.write(f'{count} checkpoint(s) expirados; apply={options["apply"]}')
