"""Content-verified, expiring render checkpoints; no editorial state lives here."""
import hashlib
import json
import logging
import shutil
import time
import uuid
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import storages

from .ffmpeg_runner import FFmpegRunner
from .media_input import RemoteMediaSource
from .workspace import DiskCapacityGuard, InsufficientWorkspaceCapacity

logger = logging.getLogger(__name__)
PREFIX = 'external_media/render-cache/v1'


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=8)
def encoder_version(binary):
    return FFmpegRunner().run([binary, '-version'])


class RenderCheckpointCache:
    def __init__(self, runner):
        self.runner = runner
        self.keys = {}

    @property
    def enabled(self):
        return (getattr(settings, 'EXTERNAL_MEDIA_RENDER_CACHE_ENABLED', True)
                and int(getattr(settings, 'EXTERNAL_MEDIA_RENDER_CACHE_TTL_SECONDS', 604800)) > 0
                and isinstance(self.runner, FFmpegRunner))

    def identity(self, source):
        if str(source) in self.keys:
            return {'checkpoint': self.keys[str(source)]}
        if getattr(source, 'render_cache_key', None):
            return {'checkpoint': source.render_cache_key}
        if isinstance(source, RemoteMediaSource):
            if not source.storage_name:
                raise ValueError('Remote source without immutable identity')
            storage = storages['external_media']
            location = str(getattr(storage, 'location', '') or '').strip('/')
            name = source.storage_name.lstrip('/')
            key = f'{location}/{name}' if location and not name.startswith(location + '/') else name
            head = storage.connection.meta.client.head_object(Bucket=storage.bucket_name, Key=key)
            if not head.get('ETag'):
                raise ValueError('Source without a verifiable object fingerprint')
            return {'name': source.storage_name, 'size': head['ContentLength'],
                    'etag': head['ETag'], 'version': head.get('VersionId')}
        path = Path(source)
        stat = path.stat()
        return {'sha256': digest_file(path), 'size': stat.st_size}

    def key(self, kind, recipe):
        if not self.enabled:
            return None
        try:
            # Invalidate when any relevant implementation or encoder changes.
            root = Path(__file__).parent
            code = [digest_file(root / name) for name in ('services.py', 'auto_reframe.py', 'render_cache.py')]
            payload = [kind, recipe, code, encoder_version(settings.FFMPEG_BINARY),
                       settings.EXTERNAL_MEDIA_INTERMEDIATE_PRESET, settings.EXTERNAL_MEDIA_INTERMEDIATE_CRF]
            return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        except Exception:
            logger.info('render_checkpoint_unavailable kind=%s', kind)
            return None

    def validate(self, path, expected_duration_ms=None):
        from .quality_control import MediaQualityService
        MediaQualityService(self.runner).validate_media(path, expected_duration_ms=expected_duration_ms, deep_audio=False).require_ok()

    def restore(self, key, destination, expected_duration_ms=None):
        if not key:
            return None
        destination = Path(destination)
        try:
            storage = storages['external_media']
            with storage.open(f'{PREFIX}/{key}.json', 'rb') as source:
                metadata = json.load(source)
            ttl = max(0, int(getattr(settings, 'EXTERNAL_MEDIA_RENDER_CACHE_TTL_SECONDS', 604800)))
            if time.time() - metadata['created'] >= ttl:
                return None
            if not metadata['name'].startswith(f'{PREFIX}/{key}/'):
                return None
            DiskCapacityGuard.require_capacity(metadata['size'], destination.parent)
            with storage.open(metadata['name'], 'rb') as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            if destination.stat().st_size != metadata['size'] or digest_file(destination) != metadata['sha256']:
                raise ValueError('Checkpoint integrity mismatch')
            self.validate(destination, expected_duration_ms)
            self.keys[str(destination)] = key
            logger.info('render_checkpoint_hit key=%s bytes=%s', key, metadata['size'])
            return metadata
        except InsufficientWorkspaceCapacity:
            destination.unlink(missing_ok=True)
            raise
        except Exception:
            destination.unlink(missing_ok=True)
            logger.info('render_checkpoint_miss key=%s', key)
            return None

    def publish(self, key, path, *, expected_duration_ms=None, **extra):
        if not key:
            return
        try:
            path = Path(path)
            if path.stat().st_size > int(getattr(settings, 'EXTERNAL_MEDIA_RENDER_CACHE_MAX_FILE_BYTES', 2 * 1024 ** 3)):
                return
            self.validate(path, expected_duration_ms)
            if not settings.USE_S3:
                DiskCapacityGuard.require_capacity(path.stat().st_size, path.parent)
            metadata = {'created': time.time(), 'size': path.stat().st_size,
                        'sha256': digest_file(path), **extra}
            storage = storages['external_media']
            with path.open('rb') as source:
                name = storage.save(f'{PREFIX}/{key}/{uuid.uuid4().hex}.mp4', File(source))
            metadata['name'] = name
            # The manifest is only visible once the complete output is saved.
            manifest = f'{PREFIX}/{key}.json'
            # A racing writer may leave an unused manifest/blob. Restore only
            # reads the canonical complete manifest; expiry cleanup removes orphans.
            if storage.exists(manifest):
                storage.delete(manifest)
            storage.save(manifest, ContentFile(json.dumps(metadata).encode()))
            self.keys[str(path)] = key
            logger.info('render_checkpoint_saved key=%s bytes=%s', key, metadata['size'])
        except Exception:
            logger.warning('render_checkpoint_publish_failed key=%s', key, exc_info=True)
