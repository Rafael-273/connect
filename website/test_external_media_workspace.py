import os
import tempfile
from datetime import timedelta
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone

from website.external_media.workspace import (
    DiskCapacityGuard,
    InsufficientWorkspaceCapacity,
    JobWorkspace,
    MediaWorkspaceGarbageCollector,
)


class MediaWorkspaceTests(TestCase):
    def test_workspace_is_removed_after_success(self):
        with tempfile.TemporaryDirectory() as root, override_settings(
            EXTERNAL_MEDIA_WORKSPACE_ROOT=root,
            EXTERNAL_MEDIA_WORKSPACE_MIN_FREE_GB=0,
        ):
            with JobWorkspace('job-1', 'test') as workspace:
                workspace.file('output', 'result.mp4').write_bytes(b'video')
                path = workspace.path
                self.assertTrue(path.exists())
            self.assertFalse(path.exists())

    def test_workspace_is_removed_after_error(self):
        with tempfile.TemporaryDirectory() as root, override_settings(
            EXTERNAL_MEDIA_WORKSPACE_ROOT=root,
            EXTERNAL_MEDIA_WORKSPACE_MIN_FREE_GB=0,
        ):
            with self.assertRaisesRegex(RuntimeError, 'render failed'):
                with JobWorkspace('job-2', 'test') as workspace:
                    path = workspace.path
                    raise RuntimeError('render failed')
            self.assertFalse(path.exists())

    def test_capacity_guard_rejects_unsafe_job(self):
        with tempfile.TemporaryDirectory() as root, override_settings(
            EXTERNAL_MEDIA_WORKSPACE_ROOT=root,
            EXTERNAL_MEDIA_WORKSPACE_MIN_FREE_GB=10 ** 9,
        ):
            with self.assertRaises(InsufficientWorkspaceCapacity):
                DiskCapacityGuard.require_capacity(1)

    def test_garbage_collector_removes_old_workspace(self):
        with tempfile.TemporaryDirectory() as root, override_settings(
            EXTERNAL_MEDIA_WORKSPACE_ROOT=root,
            EXTERNAL_MEDIA_WORKSPACE_MAX_AGE_HOURS=1,
        ):
            stale = Path(root) / 'stale-job'
            stale.mkdir()
            (stale / '.workspace.json').write_text('{}', encoding='utf-8')
            (stale / 'partial.mp4').write_bytes(b'partial')
            old = (timezone.now() - timedelta(hours=2)).timestamp()
            os.utime(stale, (old, old))
            result = MediaWorkspaceGarbageCollector.collect()
            self.assertEqual(result['removed'], 1)
            self.assertFalse(stale.exists())
