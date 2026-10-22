"""Opt-in browser checks against disposable Django/PostgreSQL fixtures."""
import os
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from website.test_editor_persistence import EditorFixture
from website.external_media.preview import TimelineRevisionService


@skipUnless(os.environ.get('FILADELFIA_PLAYWRIGHT_MODULE'), 'Set FILADELFIA_PLAYWRIGHT_MODULE to run Chrome checks')
class EditorBrowserTests(EditorFixture, StaticLiveServerTestCase):
    def browser_check(self, filename):
        env = {
            **os.environ,
            'FILADELFIA_QA_URL': self.live_server_url + reverse(
                'external_media_project_preview', args=[self.project.public_id],
            ),
            'FILADELFIA_QA_SESSION': self.client.cookies[settings.SESSION_COOKIE_NAME].value,
        }
        result = subprocess.run(
            ['node', str(Path(settings.BASE_DIR) / 'static' / 'tests' / filename)],
            env=env, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())

    def test_retained_nodes_focus_and_large_timeline(self):
        self.browser_check('editor_fluency.browser.cjs')

    def test_real_media_scrubbing_playback_and_waveforms(self):
        self.browser_check('editor_media.browser.cjs')

    def test_magnetic_alignment_precision_and_history(self):
        self.browser_check('editor_precision.browser.cjs')

    def test_framing_without_an_automatic_reframe_decision(self):
        self.preset.width = 1080
        self.preset.height = 1920
        self.preset.save(update_fields=['width', 'height'])
        self.assertFalse(any(item['type'] == 'reframe' for item in self.project.current_timeline_revision.timeline['decisions']))
        TimelineRevisionService.create_video_split(self.project, self.member, 5000)
        self.browser_check('editor_framing.browser.cjs')

    def test_optimistic_cut_deletion_and_retry(self):
        for point in (2000, 4000, 6000):
            TimelineRevisionService.create_video_split(self.project, self.member, point)
        self.browser_check('editor_cut.browser.cjs')
