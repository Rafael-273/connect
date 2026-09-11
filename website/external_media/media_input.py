"""Storage-aware media inputs for FFmpeg and FFprobe.

The object store is the source of truth in production.  FFmpeg receives a
short-lived signed URL and performs its own HTTP range requests, while local
development keeps using filesystem paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from .exceptions import ExternalMediaError
from .workspace import estimate_media_workspace_bytes, estimate_streaming_workspace_bytes


logger = logging.getLogger(__name__)


class RemoteMediaSource(str):
    """A subprocess-safe URL carrying non-secret observability metadata."""

    def __new__(cls, value, *, storage_name='', size_bytes=0):
        instance = super().__new__(cls, value)
        instance.storage_name = storage_name
        instance.size_bytes = max(0, int(size_bytes or 0))
        return instance

    def __repr__(self):
        return '<RemoteMediaSource redacted>'


@dataclass(frozen=True)
class MediaInput:
    field_file: object

    mode = 'UNKNOWN'

    @property
    def size_bytes(self):
        try:
            return max(0, int(self.field_file.size or 0))
        except (OSError, TypeError, ValueError):
            return 0

    def get_ffmpeg_input(self):
        raise NotImplementedError

    def workspace_estimate(self, *, output_count=1, needs_proxy=False):
        return estimate_media_workspace_bytes(
            self.size_bytes,
            output_count=output_count,
            needs_proxy=needs_proxy,
        )

    def materialize(self, destination, *, purpose='explicit-fallback'):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            'media_input_materialize mode=%s purpose=%s storage_name=%s bytes=%s',
            self.mode,
            purpose,
            getattr(self.field_file, 'name', ''),
            self.size_bytes,
        )
        with self.field_file.open('rb') as source, destination.open('wb') as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                target.write(chunk)
        return destination


@dataclass(frozen=True)
class StoredMediaFile:
    """Minimal FieldFile-compatible reference for transient storage objects."""

    storage: object
    name: str
    size: int = 0

    def open(self, mode='rb'):
        return self.storage.open(self.name, mode)


class LocalMediaInput(MediaInput):
    mode = 'LOCAL'

    def get_ffmpeg_input(self):
        try:
            return Path(self.field_file.path)
        except (AttributeError, NotImplementedError) as exc:
            raise ExternalMediaError(
                'O storage local não forneceu um caminho de arquivo para o FFmpeg.'
            ) from exc


class S3MediaInput(MediaInput):
    mode = 'S3_STREAM'

    def get_ffmpeg_input(self):
        storage = self.field_file.storage
        name = self.field_file.name
        expires = int(settings.EXTERNAL_MEDIA_S3_URL_EXPIRATION_SECONDS)
        try:
            location = str(getattr(storage, 'location', '') or '').strip('/')
            key = str(name).lstrip('/')
            if location and not key.startswith(f'{location}/'):
                key = f'{location}/{key}'
            url = storage.connection.meta.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': storage.bucket_name, 'Key': key},
                ExpiresIn=expires,
            )
        except Exception as exc:
            raise ExternalMediaError(
                'Não foi possível gerar acesso temporário seguro ao vídeo no S3.'
            ) from exc
        logger.info(
            'media_input_ready input_mode=S3_STREAM storage_name=%s object_bytes=%s '
            'url_expires_seconds=%s range_requests=true',
            name,
            self.size_bytes,
            expires,
        )
        return RemoteMediaSource(url, storage_name=name, size_bytes=self.size_bytes)

    def workspace_estimate(self, *, output_count=1, needs_proxy=False):
        # The original stays in S3. Only outputs and small analysis artifacts
        # consume the worker's scratch disk.
        return estimate_streaming_workspace_bytes(
            output_count=output_count,
            needs_proxy=needs_proxy,
        )

    def materialize(self, destination, *, purpose='explicit-fallback'):
        limit = int(settings.EXTERNAL_MEDIA_REMOTE_MATERIALIZE_MAX_MB * 1024 ** 2)
        if self.size_bytes > limit:
            raise ExternalMediaError(
                'Esta operação exige materialização local, mas o objeto remoto excede '
                f'o limite seguro de {settings.EXTERNAL_MEDIA_REMOTE_MATERIALIZE_MAX_MB} MB.'
            )
        return super().materialize(destination, purpose=purpose)


def media_input_factory(field_file):
    """Centralized storage-mode decision for every pipeline caller."""
    if not field_file or not getattr(field_file, 'name', ''):
        raise ExternalMediaError('O arquivo de mídia não está disponível.')
    media_input = S3MediaInput(field_file) if settings.USE_S3 else LocalMediaInput(field_file)
    logger.info(
        'media_input_selected input_mode=%s storage_name=%s',
        media_input.mode,
        field_file.name,
    )
    return media_input
