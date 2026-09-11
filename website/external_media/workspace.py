"""Ephemeral disk workspaces used by media jobs."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .exceptions import ExternalMediaError


logger = logging.getLogger(__name__)


class InsufficientWorkspaceCapacity(ExternalMediaError):
    """Raised before FFmpeg starts when the scratch disk is not safe to use."""


@dataclass(frozen=True)
class DiskCapacity:
    free_bytes: int
    required_bytes: int
    reserve_bytes: int

    @property
    def available_for_job_bytes(self):
        return max(0, self.free_bytes - self.reserve_bytes)


class DiskCapacityGuard:
    """Keeps heavyweight jobs from exhausting the worker filesystem mid-render."""

    @classmethod
    def inspect(cls, required_bytes=0, root=None):
        root = Path(root or settings.EXTERNAL_MEDIA_WORKSPACE_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(root)
        return DiskCapacity(
            free_bytes=usage.free,
            required_bytes=max(0, int(required_bytes or 0)),
            reserve_bytes=int(settings.EXTERNAL_MEDIA_WORKSPACE_MIN_FREE_GB * 1024 ** 3),
        )

    @classmethod
    def require_capacity(cls, required_bytes=0, root=None):
        capacity = cls.inspect(required_bytes, root)
        if capacity.required_bytes <= capacity.available_for_job_bytes:
            return capacity
        raise InsufficientWorkspaceCapacity(
            'Nao ha espaco temporario suficiente para iniciar este processamento. '
            f'Livre: {capacity.free_bytes / 1024 ** 3:.1f} GB; necessario com margem: '
            f'{(capacity.required_bytes + capacity.reserve_bytes) / 1024 ** 3:.1f} GB.'
        )


class JobWorkspace:
    """Isolated and self-cleaning workspace for one media operation."""

    DIRECTORIES = ('source', 'proxy', 'audio', 'frames', 'intermediate', 'output')

    def __init__(self, job_id, operation, *, estimated_bytes=0):
        self.job_id = str(job_id)
        self.operation = str(operation)
        self.estimated_bytes = max(0, int(estimated_bytes or 0))
        self.root = Path(settings.EXTERNAL_MEDIA_WORKSPACE_ROOT)
        self.path = self.root / f'{self.operation}-{self.job_id}-{uuid.uuid4().hex[:10]}'
        self.capacity = None

    def __enter__(self):
        self.capacity = DiskCapacityGuard.require_capacity(self.estimated_bytes, self.root)
        self.path.mkdir(parents=True, exist_ok=False)
        for name in self.DIRECTORIES:
            (self.path / name).mkdir(exist_ok=True)
        (self.path / '.workspace.json').write_text(json.dumps({
            'job_id': self.job_id,
            'operation': self.operation,
            'created_at': timezone.now().isoformat(),
        }), encoding='utf-8')
        logger.info(
            'media_workspace_created operation=%s job=%s free_disk_before=%s estimated_bytes=%s path=%s',
            self.operation, self.job_id, self.capacity.free_bytes, self.estimated_bytes, self.path,
        )
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.cleanup()
        return False

    def cleanup(self):
        if not self.path.exists():
            return
        try:
            workspace_size = _directory_size(self.path)
            shutil.rmtree(self.path)
            free_after = DiskCapacityGuard.inspect(root=self.root).free_bytes
            logger.info(
                'media_workspace_cleaned operation=%s job=%s workspace_size=%s free_disk_after=%s',
                self.operation, self.job_id, workspace_size, free_after,
            )
        except OSError:
            logger.exception('media_workspace_cleanup_failed operation=%s job=%s path=%s', self.operation, self.job_id, self.path)

    def file(self, directory, filename):
        if directory not in self.DIRECTORIES:
            raise ValueError(f'Workspace directory is invalid: {directory}')
        return self.path / directory / filename


class MediaWorkspaceGarbageCollector:
    """Removes abandoned workspaces left after a worker or host interruption."""

    @classmethod
    def collect(cls, *, max_age_hours=None, root=None):
        root = Path(root or settings.EXTERNAL_MEDIA_WORKSPACE_ROOT)
        if not root.exists():
            return {'removed': 0, 'skipped': 0, 'bytes_removed': 0}
        cutoff = timezone.now() - timedelta(hours=max_age_hours or settings.EXTERNAL_MEDIA_WORKSPACE_MAX_AGE_HOURS)
        removed = skipped = bytes_removed = 0
        for path in root.iterdir():
            marker = path / '.workspace.json'
            if not path.is_dir() or not marker.exists():
                continue
            if cls._belongs_to_active_job(marker):
                skipped += 1
                continue
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone())
            if modified_at > cutoff:
                skipped += 1
                continue
            size = _directory_size(path)
            try:
                shutil.rmtree(path)
                removed += 1
                bytes_removed += size
                logger.warning('media_workspace_garbage_collected path=%s workspace_size=%s', path, size)
            except OSError:
                logger.exception('media_workspace_garbage_collection_failed path=%s', path)
        return {'removed': removed, 'skipped': skipped, 'bytes_removed': bytes_removed}

    @staticmethod
    def _belongs_to_active_job(marker):
        try:
            job_id = json.loads(marker.read_text(encoding='utf-8')).get('job_id')
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        if not job_id:
            return False
        # Imports stay local to keep the workspace infrastructure independent at startup.
        from website.models.external_media import ExternalMediaJob, ExternalMediaProject

        try:
            project_active = ExternalMediaProject.objects.filter(
                public_id=job_id,
                status__in=[
                    ExternalMediaProject.Status.PENDING,
                    ExternalMediaProject.Status.ASSEMBLING,
                    ExternalMediaProject.Status.PROCESSING,
                ],
            ).exists()
            job_active = ExternalMediaJob.objects.filter(
                public_id=job_id,
            ).exclude(
                status__in=[
                    ExternalMediaJob.Status.FINISHED,
                    ExternalMediaJob.Status.ERROR,
                    ExternalMediaJob.Status.CANCELLED,
                ],
            ).exists()
        except (ValidationError, ValueError, TypeError):
            return False
        return project_active or job_active


def estimate_media_workspace_bytes(source_bytes, *, output_count=1, needs_proxy=False):
    """Conservative peak estimate: downloaded sources plus intermediate/final outputs."""
    source_bytes = max(0, int(source_bytes or 0))
    multiplier = 2.0 + max(1, int(output_count or 1))
    if needs_proxy:
        multiplier += 0.35
    return int(source_bytes * multiplier * settings.EXTERNAL_MEDIA_WORKSPACE_SAFETY_FACTOR)


def estimate_streaming_workspace_bytes(*, output_count=1, needs_proxy=False):
    """Estimate scratch outputs without charging the remote input against disk."""
    output_count = max(1, int(output_count or 1))
    estimate_mb = settings.EXTERNAL_MEDIA_STREAMING_WORKSPACE_ESTIMATE_MB
    multiplier = output_count + (0.35 if needs_proxy else 0)
    return int(estimate_mb * 1024 ** 2 * multiplier * settings.EXTERNAL_MEDIA_WORKSPACE_SAFETY_FACTOR)


def _directory_size(path):
    return sum(item.stat().st_size for item in path.rglob('*') if item.is_file())
