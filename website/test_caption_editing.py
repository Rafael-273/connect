import json
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase
from django.urls import reverse

from website.external_media.preview import TimelineRevisionService
from website.external_media.services import SubtitleService
from website.models.external_media import SubtitleCue, SubtitleTrack
from website.test_editor_persistence import EditorFixture


class CaptionEditingTests(EditorFixture, TestCase):
    def test_split_creates_two_independently_editable_cues_and_survives_reload(self):
        revision, right_id = TimelineRevisionService.split_subtitle(self.project, self.member, self.cue.pk, 1500)
        left, right = SubtitleCue.objects.get(pk=self.cue.pk), SubtitleCue.objects.get(pk=right_id)
        self.assertEqual((left.start_ms, left.end_ms, left.text), (1000, 1500, 'Um'))
        self.assertEqual((right.start_ms, right.end_ms, right.text), (1500, 2000, 'Um'))
        TimelineRevisionService.update_subtitle(self.project, self.member, right.pk, text='Outra frase')
        left.refresh_from_db()
        self.assertEqual(left.text, 'Um')
        self.assertIn(right_id, [cue['id'] for cue in revision.timeline['captions']])
        page = self.client.get(reverse('external_media_project_preview', args=[self.project.public_id]))
        self.assertContains(page, 'Outra frase')

    def test_synchronized_bilingual_pair_is_split_at_the_same_time(self):
        english = SubtitleTrack.objects.create(job=self.project.render_job, language='en')
        translated = SubtitleCue.objects.create(track=english, cue_index=1, start_ms=1000, end_ms=2000, text='One')
        _, right_id = TimelineRevisionService.split_subtitle(self.project, self.member, self.cue.pk, 1500)
        right = SubtitleCue.objects.get(pk=right_id)
        translated.refresh_from_db()
        self.assertEqual(translated.end_ms, 1500)
        peer = SubtitleCue.objects.get(track=english, cue_index=right.cue_index)
        self.assertEqual((peer.start_ms, peer.end_ms, peer.text), (1500, 2000, 'One'))

    def test_split_requires_one_frame_on_each_side_and_is_atomic(self):
        count = self.project.timeline_revisions.count()
        for point in (1000, 1010, 1999, 2000, 3000):
            with self.subTest(point=point), self.assertRaises(ValueError):
                TimelineRevisionService.split_subtitle(self.project, self.member, self.cue.pk, point)
        self.assertEqual(self.project.timeline_revisions.count(), count)
        self.cue.refresh_from_db()
        self.assertEqual(self.cue.end_ms, 2000)

    def test_paste_creates_distinct_cues_without_changing_the_original(self):
        ids = []
        for start in (5000, 6500):
            revision, cue_id = TimelineRevisionService.create_subtitle(
                self.project, self.member, self.cue.track_id, self.cue.text, start, 1000,
            )
            ids.append(cue_id)
            self.assertIn(cue_id, [cue['id'] for cue in revision.timeline['captions']])
        self.assertEqual(len(set(ids)), 2)
        TimelineRevisionService.update_subtitle(self.project, self.member, ids[0], text='Nova legenda')
        self.cue.refresh_from_db()
        self.assertEqual((self.cue.text, self.cue.start_ms, self.cue.end_ms), ('Um', 1000, 2000))
        self.assertEqual(SubtitleCue.objects.get(pk=ids[1]).text, 'Um')

    def test_paste_refuses_overlap_and_invalid_positions_without_overwriting(self):
        count = self.project.timeline_revisions.count()
        for start, duration in [(1000, 1000), (1500, 1000), (-1, 1000), (10000, 1000), (5000, 1)]:
            with self.subTest(start=start), self.assertRaises(ValueError):
                TimelineRevisionService.create_subtitle(self.project, self.member, self.cue.track_id, 'Cópia', start, duration)
        self.assertEqual(self.project.timeline_revisions.count(), count)
        self.assertEqual(SubtitleCue.objects.filter(track=self.cue.track).count(), 2)

    def test_split_and_paste_have_undo_redo_and_do_not_reuse_deleted_indexes(self):
        _, split_id = TimelineRevisionService.split_subtitle(self.project, self.member, self.cue.pk, 1500)
        TimelineRevisionService.navigate_history(self.project, self.member, 'undo')
        self.assertFalse(SubtitleCue.objects.filter(pk=split_id).exists())
        self.cue.refresh_from_db()
        self.assertEqual(self.cue.end_ms, 2000)
        TimelineRevisionService.navigate_history(self.project, self.member, 'redo')
        self.assertTrue(SubtitleCue.objects.filter(pk=split_id).exists())
        _, pasted_id = TimelineRevisionService.create_subtitle(self.project, self.member, self.cue.track_id, 'Cópia', 5000, 1000)
        TimelineRevisionService.navigate_history(self.project, self.member, 'undo')
        self.assertFalse(SubtitleCue.objects.filter(pk=pasted_id).exists())
        TimelineRevisionService.navigate_history(self.project, self.member, 'redo')
        self.assertTrue(SubtitleCue.objects.filter(pk=pasted_id).exists())

    def test_pasted_translation_is_included_in_bilingual_render(self):
        english = SubtitleTrack.objects.create(job=self.project.render_job, language='en')
        _, cue_id = TimelineRevisionService.create_subtitle(self.project, self.member, english.pk, 'Pasted English', 5000, 1000)
        with TemporaryDirectory() as directory:
            output = Path(directory) / 'captions.ass'
            SubtitleService().write_dual_ass([self.cue.track, english], output, self.style, 1920, 1080)
            self.assertIn('Pasted English', output.read_text(encoding='utf-8-sig'))

    def test_api_split_paste_retry_and_track_scope(self):
        split_url = reverse('external_media_project_preview_subtitle', args=[self.project.public_id, self.cue.pk])
        headers = {'HTTP_X_EDITOR_VERSION': str(self.base), 'HTTP_X_EDITOR_OPERATION': str(uuid.uuid4())}
        response = self.client.post(split_url, json.dumps({'split_at_ms': 1500}), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['created_cue_id'])
        url = reverse('external_media_project_preview_subtitle_create', args=[self.project.public_id])
        headers = {'HTTP_X_EDITOR_VERSION': str(response.json()['editorial_version']), 'HTTP_X_EDITOR_OPERATION': str(uuid.uuid4())}
        body = json.dumps({'track_id': self.cue.track_id, 'text': 'Colada', 'start_ms': 5000, 'duration_ms': 1000})
        first = self.client.post(url, body, content_type='application/json', **headers)
        self.assertEqual(first.status_code, 200)
        retry = self.client.post(url, body, content_type='application/json', **headers)
        self.assertEqual(first.json(), retry.json())
        foreign = SubtitleTrack.objects.create(job=self.make_job(), language='pt')
        body = json.dumps({'track_id': foreign.pk, 'text': 'Outra', 'start_ms': 7000, 'duration_ms': 1000})
        headers = {'HTTP_X_EDITOR_VERSION': str(first.json()['editorial_version']), 'HTTP_X_EDITOR_OPERATION': str(uuid.uuid4())}
        self.assertEqual(self.client.post(url, body, content_type='application/json', **headers).status_code, 404)
