import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from website.external_media.canonical import SourceManifestBuilder
from website.external_media.preview import TimelineRevisionService
from website.models import MinistryMembership
from website.models.external_media import (
    ExternalMediaProject, MediaTemplate, MediaTemplateBlock, MediaTemplateVersion,
    PreviewOperation, ProjectBlockMedia, SubtitleCue, SubtitleTrack,
)
from website.test_external_media import ExternalMediaFixtureMixin
from website.views.external_media import ExternalMediaProjectPreviewSubtitleView


class EditorFixture(ExternalMediaFixtureMixin):
    def setUp(self):
        super().setUp()
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        template = MediaTemplate.objects.create(name='Editor', slug='editor-persistence')
        version = MediaTemplateVersion.objects.create(
            template=template, version=1, preset=self.preset, subtitle_style=self.style,
        )
        block = MediaTemplateBlock.objects.create(version=version, key='video', name='Vídeo', order=1)
        self.project = ExternalMediaProject.objects.create(
            name='Editor', template_version=version, created_by=self.member,
            status=ExternalMediaProject.Status.AWAITING_REVIEW, render_job=self.make_job(),
        )
        ProjectBlockMedia.objects.create(
            project=self.project, block=block, position=1, original_filename='take.mp4', duration_ms=10000,
            file=SimpleUploadedFile('take.mp4', b'fake-video'),
        )
        self.project.configuration = {'source_manifest': SourceManifestBuilder.build(self.project)}
        self.project.save(update_fields=['configuration'])
        track = SubtitleTrack.objects.create(job=self.project.render_job, language='pt', is_source=True)
        self.cue = SubtitleCue.objects.create(track=track, cue_index=1, start_ms=1000, end_ms=2000, text='Um')
        self.other = SubtitleCue.objects.create(track=track, cue_index=2, start_ms=3000, end_ms=4000, text='Dois')
        TimelineRevisionService.ensure_initial(self.project, self.member)
        self.project.refresh_from_db()
        self.base = self.project.editorial_version
        self.client.force_login(self.user)

    def post(self, cue, body, version=None, operation=None):
        return self.client.post(
            reverse('external_media_project_preview_subtitle', args=[self.project.public_id, cue.pk]),
            json.dumps(body), content_type='application/json',
            HTTP_X_EDITOR_VERSION=str(self.base if version is None else version),
            HTTP_X_EDITOR_OPERATION=str(operation or uuid.uuid4()),
        )


class EditorPersistenceTests(EditorFixture, TestCase):
    def test_cached_editor_media_survives_reopening_without_enqueuing_another_job(self):
        from unittest.mock import patch
        from django.core.files.base import ContentFile
        from website.external_media.preview_artifacts import PreviewArtifactService
        source = self.project.render_job.original_video
        with patch.object(PreviewArtifactService, 'enqueue') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                first = PreviewArtifactService.request(self.project, 'waveform', source, version='v1')
                duplicate = PreviewArtifactService.request(self.project, 'waveform', source, version='v1')
            self.assertEqual(first.pk, duplicate.pk)
            enqueue.assert_called_once()
            first.file.save('peaks.json', ContentFile(b'{"schema":"connect.waveform.v1","durationMs":1000,"peaks":[0.5]}'), save=False)
            self.addCleanup(first.file.delete, save=False)
            first.status = 'READY'
            first.save(update_fields=['file', 'status'])
            with self.captureOnCommitCallbacks(execute=True):
                reopened = PreviewArtifactService.request(self.project, 'waveform', source, version='v1')
            self.assertEqual(reopened.pk, first.pk)
            self.assertEqual(reopened.file.name, first.file.name)
            enqueue.assert_called_once()

    def test_volume_and_mix_edits_reuse_the_same_picture_without_enqueuing_ffmpeg(self):
        from unittest.mock import patch
        from website.external_media.preview import ProjectProxyService
        initial = self.project.current_timeline_revision
        with patch.object(TimelineRevisionService, '_enqueue_timeline_review_proxy') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                volume = TimelineRevisionService.update_music_volume(self.project, self.member, .42)
                self.project.refresh_from_db()
                mix = TimelineRevisionService.update_audio_mix(self.project, self.member, {
                    'ducking': {'enabled': True, 'base_duck_db': 6},
                    'music_keyframes': [{'time_ms': 1200, 'gain_db': -3}],
                })
            enqueue.assert_not_called()
        self.assertEqual(volume.timeline['music_volume'], .42)
        self.assertEqual(mix.timeline['audio_mix']['music_keyframes'], [{'time_ms': 1200, 'gain_db': -3.0}])
        for changed in (volume, mix):
            self.assertEqual(ProjectProxyService.timeline_review_proxy_id(changed), ProjectProxyService.timeline_review_proxy_id(initial))

    def test_adding_music_preserves_split_clips_and_places_only_the_new_source(self):
        clips=[
            {'clip_id':'left','track_id':123,'start_ms':0,'end_ms':2000,'source_in_ms':500,'volume':.25,'audio_transition':None},
            {'clip_id':'right','track_id':123,'start_ms':4000,'end_ms':10000,'source_in_ms':2500,'volume':.8,'audio_transition':None},
        ]
        before=TimelineRevisionService.update_music_tracks(self.project,self.member,clips)
        self.project.refresh_from_db()
        original=before.timeline['music_tracks']
        revision=TimelineRevisionService.update_music_tracks(self.project,self.member,[*original,{'track_id':124}])
        after={row['clip_id']:row for row in revision.timeline['music_tracks']}
        for row in original:
            self.assertEqual(after[row['clip_id']],row)
        added=next(row for row in after.values() if row['track_id']==124)
        self.assertEqual((added['start_ms'],added['end_ms']),(2000,4000))

    def test_soundtrack_changes_reuse_video_proxy_without_enqueuing_video_render(self):
        from unittest.mock import patch
        from website.external_media.preview import ProjectProxyService
        current = self.project.current_timeline_revision
        with patch.object(TimelineRevisionService, '_enqueue_timeline_review_proxy') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                revision = TimelineRevisionService.update_music_tracks(self.project, self.member, [
                    {'track_id': 123, 'start_ms': 0, 'end_ms': 5000},
                    {'track_id': 124, 'start_ms': 5000, 'end_ms': 10000,
                     'audio_transition': {'type': 'CONSTANT_POWER', 'from_track_id': 123, 'duration_ms': 2000}},
                ])
            enqueue.assert_not_called()
        self.assertEqual(ProjectProxyService.timeline_review_proxy_id(revision), ProjectProxyService.timeline_review_proxy_id(current))
        changed = TimelineRevisionService.update_music_tracks(self.project, self.member, [])
        self.assertEqual(changed.timeline['review_video_revision_id'], current.pk)

    def test_manual_framing_can_start_without_an_automatic_decision(self):
        current = self.project.current_timeline_revision
        source_id = current.source_manifest['sources'][0]['id']
        TimelineRevisionService.create_video_split(self.project, self.member, 5000)
        revision = TimelineRevisionService.update_transform(
            self.project, self.member, f'reframe-source-{source_id}', {
                'scale': 1.35, 'x': .2, 'y': -.1,
                'segment_start_ms': 0, 'segment_end_ms': 5000,
            },
        )
        clips = revision.timeline['clips']
        self.assertEqual(clips[0]['timeline_out_ms'], 5000)
        transform = next(item for item in clips[0]['effects'] if item['type'] == 'transform')
        self.assertEqual(transform['scale'], 1.35)
        self.assertFalse(any(item['type'] == 'transform' for item in clips[1]['effects']))

    def test_manual_framing_rejects_a_source_outside_the_revision(self):
        with self.assertRaisesMessage(ValueError, 'Enquadramento não encontrado'):
            TimelineRevisionService.update_transform(
                self.project, self.member, 'reframe-source-unknown', {'scale': 1.35},
            )
        self.project.refresh_from_db()
        self.assertEqual(self.project.editorial_version, self.base)

    def test_sequential_edits_survive_reload_and_preserve_other_cues(self):
        first = self.post(self.cue, {'end_ms': 1800})
        self.assertEqual(first.status_code, 200)
        second = self.post(self.other, {'end_ms': 3800}, first.json()['editorial_version'])
        self.assertEqual(second.status_code, 200)
        self.project.refresh_from_db()
        captions = {item['id']: item for item in self.project.current_timeline_revision.timeline['captions']}
        self.assertEqual(captions[self.cue.pk]['end_ms'], 1800)
        self.assertEqual(captions[self.other.pk]['end_ms'], 3800)
        self.cue.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual((self.cue.end_ms, self.other.end_ms), (1800, 3800))

    def test_stale_command_cannot_overwrite_a_newer_edit(self):
        self.assertEqual(self.post(self.cue, {'end_ms': 1700}).status_code, 200)
        self.assertEqual(self.post(self.cue, {'end_ms': 1900}).status_code, 409)
        self.cue.refresh_from_db()
        self.assertEqual(self.cue.end_ms, 1700)

    def test_retry_after_commit_returns_receipt_without_duplicate_revision(self):
        operation = uuid.uuid4()
        first = self.post(self.cue, {'end_ms': 1800}, operation=operation)
        count = self.project.timeline_revisions.count()
        retry = self.post(self.cue, {'end_ms': 1800}, operation=operation)
        self.assertEqual(first.json(), retry.json())
        self.assertEqual(self.project.timeline_revisions.count(), count)
        self.assertEqual(PreviewOperation.objects.filter(project=self.project).count(), 1)

    def test_operation_id_cannot_be_reused_with_different_content(self):
        operation = uuid.uuid4()
        self.post(self.cue, {'end_ms': 1800}, operation=operation)
        self.assertEqual(self.post(self.cue, {'end_ms': 1900}, operation=operation).status_code, 409)

    def test_invalid_save_creates_no_receipt_and_does_not_advance_version(self):
        self.assertEqual(self.post(self.cue, {'end_ms': 1000}).status_code, 400)
        self.project.refresh_from_db()
        self.assertEqual(self.project.editorial_version, self.base)
        self.assertFalse(PreviewOperation.objects.filter(project=self.project).exists())

    def test_undo_moves_revision_back_but_increases_concurrency_version(self):
        before = self.project.current_timeline_revision_id
        saved = self.post(self.cue, {'end_ms': 1800}).json()
        undone = self.client.post(
            reverse('external_media_project_preview_undo', args=[self.project.public_id]), '{}',
            content_type='application/json', HTTP_X_EDITOR_VERSION=str(saved['editorial_version']),
            HTTP_X_EDITOR_OPERATION=str(uuid.uuid4()),
        )
        self.assertEqual(undone.status_code, 200)
        self.assertGreater(undone.json()['editorial_version'], saved['editorial_version'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.current_timeline_revision_id, before)
        self.cue.refresh_from_db()
        self.assertEqual(self.cue.end_ms, 2000)
        self.assertEqual(self.post(self.cue, {'end_ms': 1900}, saved['editorial_version']).status_code, 409)

    def test_edits_are_rejected_while_final_render_is_in_progress(self):
        self.project.status = ExternalMediaProject.Status.PROCESSING
        self.project.save(update_fields=['status'])
        self.assertEqual(self.post(self.cue, {'end_ms': 1800}).status_code, 409)
        self.cue.refresh_from_db()
        self.assertEqual(self.cue.end_ms, 2000)

    def test_legacy_client_cannot_change_cues_during_render(self):
        self.project.status = ExternalMediaProject.Status.PROCESSING
        self.project.save(update_fields=['status'])
        response = self.client.post(
            reverse('external_media_project_preview_subtitle', args=[self.project.public_id, self.cue.pk]),
            json.dumps({'end_ms': 1800}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 409)

    def test_approval_pins_the_confirmed_revision_and_blocks_later_edits(self):
        saved = self.post(self.cue, {'end_ms': 1800}).json()
        approval_url = reverse('external_media_project_preview_approve', args=[self.project.public_id])
        stale = self.client.post(
            approval_url, '{}', content_type='application/json',
            HTTP_X_EDITOR_VERSION=str(self.base), HTTP_X_EDITOR_OPERATION=str(uuid.uuid4()),
        )
        self.assertEqual(stale.status_code, 409)
        approved = self.client.post(
            approval_url, '{}', content_type='application/json',
            HTTP_X_EDITOR_VERSION=str(saved['editorial_version']), HTTP_X_EDITOR_OPERATION=str(uuid.uuid4()),
        )
        self.assertEqual(approved.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.current_timeline_revision_id, self.project.approved_timeline_revision_id)
        self.assertEqual(approved.json()['approved_revision'], saved['revision'])
        self.assertEqual(self.post(self.cue, {'end_ms': 1900}, saved['editorial_version']).status_code, 409)

    def test_partial_overlay_preserves_other_position_fields(self):
        revision = TimelineRevisionService.mutate_overlay(self.project, self.member, 'text-1', {
            'type': 'TEXT', 'start_ms': 1000, 'end_ms': 2000, 'content': {'text': 'Texto'},
            'position': {'x': .5, 'y': .7, 'width': .4},
        }, create=True)
        updated = TimelineRevisionService.mutate_overlay(self.project, self.member, 'text-1', {'position': {'x': .8}})
        overlay = next(item for item in updated.timeline['overlays'] if item['id'] == 'text-1')
        self.assertEqual(overlay['position'], {'x': .8, 'y': .7, 'width': .4})
        self.assertEqual((overlay['start_ms'], overlay['end_ms']), (1000, 2000))


class EditorConcurrentPersistenceTests(EditorFixture, TransactionTestCase):
    def test_same_base_concurrent_requests_only_commit_one_operation(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locks')
        for same_element in (False, True):
            with self.subTest(same_element=same_element):
                self.project.refresh_from_db()
                base = self.project.editorial_version
                barrier = Barrier(2)

                def edit(cue_id, end):
                    close_old_connections()
                    try:
                        request = RequestFactory().post(
                            '/preview/subtitles/', json.dumps({'end_ms': end}), content_type='application/json',
                            HTTP_X_EDITOR_VERSION=str(base), HTTP_X_EDITOR_OPERATION=str(uuid.uuid4()),
                        )
                        request.user = self.user
                        barrier.wait(timeout=10)
                        return ExternalMediaProjectPreviewSubtitleView.as_view()(
                            request, public_id=self.project.public_id, cue_id=cue_id,
                        ).status_code
                    finally:
                        close_old_connections()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(edit, self.cue.pk, 1550 if same_element else 1600)
                    second = pool.submit(edit, self.cue.pk if same_element else self.other.pk, 1800 if same_element else 3600)
                    self.assertEqual(sorted([first.result(timeout=20), second.result(timeout=20)]), [200, 409])
