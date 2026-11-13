import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from website.external_media.caption_visibility import visible_caption_tracks
from website.external_media.preview import TimelineRevisionService
from website.external_media.timeline import InternalTimelineBuilder
from website.models.external_media import SubtitleTrack
from website.test_editor_persistence import EditorFixture


class CaptionVisibilityTests(EditorFixture, TestCase):
    def test_visibility_is_revisioned_and_used_for_outputs(self):
        translated = SubtitleTrack.objects.create(job=self.project.render_job, language='en')
        url = reverse('external_media_project_preview_caption_visibility', args=[self.project.public_id])
        with patch.object(TimelineRevisionService, '_enqueue_timeline_review_proxy'):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, json.dumps({'track_id': self.cue.track_id, 'visible': False}),
                                            content_type='application/json', HTTP_X_EDITOR_VERSION=str(self.base))
        self.assertEqual(response.status_code, 200, response.content)
        self.project.refresh_from_db()
        revision = self.project.current_timeline_revision
        self.assertEqual(revision.timeline['hidden_caption_track_ids'], [self.cue.track_id])
        self.assertEqual([t.pk for t in visible_caption_tracks([self.cue.track, translated], revision)], [translated.pk])
        with TemporaryDirectory() as directory:
            (Path(directory) / 'Captions').mkdir()
            captions, _ = InternalTimelineBuilder()._captions(self.project, Path(directory))
            self.assertEqual([caption['language'] for caption in captions], ['en'])
        with patch.object(TimelineRevisionService, '_enqueue_timeline_review_proxy'):
            next_revision = TimelineRevisionService.update_music_volume(self.project, self.member, .37)
        self.assertEqual(next_revision.timeline['hidden_caption_track_ids'], [self.cue.track_id])
        restored = TimelineRevisionService.update_caption_visibility(self.project, self.member, self.cue.track_id, True)
        self.assertEqual(restored.timeline['hidden_caption_track_ids'], [])
        self.assertTrue(self.cue.track.cues.exists())

    def test_rejects_foreign_track_and_non_boolean(self):
        foreign = SubtitleTrack.objects.create(job=self.make_job(), language='en')
        for track_id, visible in [(foreign.pk, False), (self.cue.track_id, 'false')]:
            with self.assertRaises(ValueError):
                TimelineRevisionService.update_caption_visibility(self.project, self.member, track_id, visible)
