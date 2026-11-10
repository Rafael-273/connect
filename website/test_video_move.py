import json
import shutil
import subprocess
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from website.external_media.preview import TimelineRevisionService, ProjectProxyService
from website.external_media.services import AssemblySource, ExternalMediaProjectPipeline, SubtitleService, VideoAssemblyService
from website.external_media.video_order import clip_range
from website.models.external_media import SubtitleCue, SubtitleTrack, ProjectBlockMedia, ProjectSourceProxy
from django.core.files.uploadedfile import SimpleUploadedFile
from website.external_media.canonical import SourceManifestBuilder
from website.test_editor_persistence import EditorFixture


class VideoMoveTests(EditorFixture, TestCase):
    def setUp(self):
        super().setUp()
        for point in (3000, 6000):
            TimelineRevisionService.create_video_split(self.project, self.member, point)
        self.project.refresh_from_db()
        self.original = self.project.current_timeline_revision
        self.source = self.original.timeline['clips'][0]['asset_id']

    def move_middle_to_end(self):
        return TimelineRevisionService.move_video(self.project, self.member, self.source, 3000, 6000)

    def test_move_keeps_linked_video_audio_captions_and_delivery_order(self):
        revision = self.move_middle_to_end()
        self.assertEqual([clip['master_in_ms'] for clip in revision.timeline['clips']], [0, 6000, 3000])
        self.assertEqual([(clip['timeline_in_ms'], clip['timeline_out_ms']) for clip in revision.timeline['clips']],
                         [(0, 3000), (3000, 7000), (7000, 10000)])
        self.assertEqual(revision.timeline['sequence']['duration_ms'], 10000)
        self.other.refresh_from_db()
        self.assertEqual((self.other.start_ms, self.other.end_ms), (7000, 8000))
        intervals = ExternalMediaProjectPipeline._approved_source_intervals(
            revision, [AssemblySource(Path('take.mp4'), label=self.source)],
        )
        self.assertEqual([(item['start_ms'], item['end_ms']) for item in intervals],
                         [(0, 3000), (6000, 10000), (3000, 6000)])
        filters, count = ProjectProxyService._timeline_review_filters(revision, None)
        self.assertEqual(count, 3)
        graph = ';'.join(filters)
        self.assertIn('trim=start=6.000:end=10.000', graph)
        self.assertIn('atrim=start=6.000:end=10.000', graph)
        self.assertLess(graph.index('trim=start=6.000:end=10.000'), graph.index('trim=start=3.000:end=6.000'))

    def test_move_to_start_and_insert_before_another_take(self):
        before = clip_range(self.original.timeline['clips'][0])
        first = TimelineRevisionService.move_video(self.project, self.member, self.source, 6000, 10000, before)
        self.assertEqual([clip['master_in_ms'] for clip in first.timeline['clips']], [6000, 0, 3000])
        before = clip_range(first.timeline['clips'][2])
        second = TimelineRevisionService.move_video(self.project, self.member, self.source, 6000, 10000, before)
        self.assertEqual([clip['master_in_ms'] for clip in second.timeline['clips']], [0, 6000, 3000])

    def test_move_can_reorder_different_video_sources(self):
        ProjectBlockMedia.objects.create(
            project=self.project, block=self.project.block_media.first().block, position=2,
            duration_ms=2000, original_filename='ending.mp4', file=SimpleUploadedFile('ending.mp4', b'ending'),
        )
        manifest = SourceManifestBuilder.build(self.project)
        parent = self.project.current_timeline_revision
        revision = TimelineRevisionService._create(self.project, manifest, parent.edit_decision_set, 'Mais um vídeo', self.member, parent)
        end = revision.timeline['clips'][-1]
        moved = TimelineRevisionService.move_video(self.project, self.member, end['asset_id'], end['master_in_ms'], end['master_out_ms'], clip_range(revision.timeline['clips'][0]))
        self.assertEqual(moved.timeline['clips'][0]['asset_id'], end['asset_id'])
        self.assertEqual(moved.timeline['sequence']['duration_ms'], 12000)

    def test_moved_take_keeps_its_overlays_and_broll_ranges(self):
        parent = self.original
        TimelineRevisionService._create(
            self.project, parent.source_manifest, parent.edit_decision_set, 'Elementos', self.member, parent,
            overlays=[{'id': 'title', 'start_ms': 3500, 'end_ms': 4500}],
            brolls=[{'id': 'element', 'start_ms': 4000, 'end_ms': 5000, 'source_in_ms': 200}],
        )
        revision = self.move_middle_to_end()
        self.assertEqual((revision.timeline['overlays'][0]['start_ms'], revision.timeline['overlays'][0]['end_ms']), (7500, 8500))
        self.assertEqual((revision.timeline['brolls'][0]['start_ms'], revision.timeline['brolls'][0]['end_ms']), (8000, 9000))
        self.assertEqual(revision.timeline['brolls'][0]['source_in_ms'], 200)

    def test_move_survives_other_edits_splits_reload_and_undo_redo(self):
        self.move_middle_to_end()
        split = TimelineRevisionService.create_video_split(self.project, self.member, 4500)
        self.assertEqual([clip['master_in_ms'] for clip in split.timeline['clips']], [0, 6000, 3000, 4500])
        changed = TimelineRevisionService.update_subtitle(self.project, self.member, self.other.pk, text='Movida')
        self.assertEqual(changed.timeline['clips'], split.timeline['clips'])
        TimelineRevisionService.navigate_history(self.project, self.member, 'undo')
        TimelineRevisionService.navigate_history(self.project, self.member, 'undo')
        restored = TimelineRevisionService.navigate_history(self.project, self.member, 'undo')
        self.assertEqual([clip['master_in_ms'] for clip in restored.timeline['clips']], [0, 3000, 6000])
        self.other.refresh_from_db()
        self.assertEqual(self.other.start_ms, 3000)
        redone = TimelineRevisionService.navigate_history(self.project, self.member, 'redo')
        self.assertEqual([clip['master_in_ms'] for clip in redone.timeline['clips']], [0, 6000, 3000])
        page = self.client.get(reverse('external_media_project_preview', args=[self.project.public_id]))
        self.assertTrue(page.context['timeline']['video_reordered'])
        self.assertIsNone(page.context['timeline']['review_master_url'])

    def test_captions_crossing_a_moved_boundary_are_split_and_undoable(self):
        cross = SubtitleCue.objects.create(track=self.cue.track, cue_index=10, start_ms=5000, end_ms=7000, text='Entre takes')
        # Freeze this cue in the parent revision used by history.
        TimelineRevisionService.update_subtitle(self.project, self.member, cross.pk, text='Entre os takes')
        revision = self.move_middle_to_end()
        parts = [item for item in revision.timeline['captions'] if item['text'] == 'Entre os takes']
        self.assertEqual(sorted((item['start_ms'], item['end_ms']) for item in parts), [(3000, 4000), (9000, 10000)])
        TimelineRevisionService.navigate_history(self.project, self.member, 'undo')
        self.assertEqual(SubtitleCue.objects.filter(track=self.cue.track, text='Entre os takes').count(), 1)
        cross.refresh_from_db()
        self.assertEqual((cross.start_ms, cross.end_ms), (5000, 7000))

    def test_split_bilingual_captions_keep_matching_pair_indexes(self):
        english = SubtitleTrack.objects.create(job=self.project.render_job, language='en')
        for track in (self.cue.track, english):
            SubtitleCue.objects.create(track=track, cue_index=10, start_ms=5000, end_ms=7000, text='Par')
        SubtitleCue.objects.create(track=english, cue_index=20, start_ms=8000, end_ms=9000, text='Outra')
        parent = self.project.current_timeline_revision
        TimelineRevisionService._create(self.project, parent.source_manifest, parent.edit_decision_set, 'Legendas', self.member, parent)
        self.move_middle_to_end()
        pairs = list(SubtitleCue.objects.filter(track__job=self.project.render_job, text='Par').values_list('cue_index', 'start_ms', 'end_ms'))
        self.assertEqual(len(pairs), 4)
        self.assertEqual(len(set(pairs)), 2)

    def test_subtitle_exports_follow_new_timeline_order(self):
        self.move_middle_to_end()
        third = SubtitleCue.objects.create(track=self.cue.track, cue_index=3, start_ms=3500, end_ms=4000, text='QR Code')
        with TemporaryDirectory() as directory:
            for suffix, writer in [('srt', SubtitleService().write_srt), ('vtt', SubtitleService().write_vtt)]:
                output = Path(directory) / f'captions.{suffix}'
                writer(self.cue.track, output, self.style)
                content = output.read_text()
                self.assertLess(content.index(third.text), content.index(self.other.text))

    def test_cut_after_move_uses_master_range_and_shifts_the_correct_captions(self):
        self.move_middle_to_end()
        revision = TimelineRevisionService.create_manual_cut(self.project, self.member, 3000, 6000)
        self.assertEqual([clip['master_in_ms'] for clip in revision.timeline['clips']], [0, 6000])
        self.assertEqual(revision.timeline['sequence']['duration_ms'], 7000)
        self.assertFalse(SubtitleCue.objects.filter(pk=self.other.pk).exists())
        self.cue.refresh_from_db()
        self.assertEqual((self.cue.start_ms, self.cue.end_ms), (1000, 2000))

    def test_invalid_and_noop_moves_do_not_create_revisions(self):
        count = self.project.timeline_revisions.count()
        for source, start, end, before in [
            ('foreign-source', 3000, 6000, None), (self.source, 100, 200, None),
            (self.source, 3000, 6000, {'source_id': self.source, 'start_ms': 1, 'end_ms': 2}),
        ]:
            with self.assertRaises(ValueError):
                TimelineRevisionService.move_video(self.project, self.member, source, start, end, before)
        TimelineRevisionService.move_video(self.project, self.member, self.source, 6000, 10000)
        self.assertEqual(self.project.timeline_revisions.count(), count)

    def test_move_api_persists_once_and_rejects_stale_edits(self):
        url = reverse('external_media_project_preview_video_move', args=[self.project.public_id])
        body = json.dumps({'source_id': self.source, 'start_ms': 3000, 'end_ms': 6000})
        headers = {'HTTP_X_EDITOR_VERSION': str(self.project.editorial_version), 'HTTP_X_EDITOR_OPERATION': str(uuid.uuid4())}
        response = self.client.post(url, body, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200)
        retry = self.client.post(url, body, content_type='application/json', **headers)
        self.assertEqual(response.json(), retry.json())
        headers['HTTP_X_EDITOR_OPERATION'] = str(uuid.uuid4())
        self.assertEqual(self.client.post(url, body, content_type='application/json', **headers).status_code, 409)

    def test_review_master_keeps_original_clock_after_final_delivery(self):
        from website.external_media.preview import REVIEW_MASTER_PROXY_ID
        proxy = ProjectSourceProxy.objects.create(
            project=self.project, source_id=REVIEW_MASTER_PROXY_ID,
            profile=ProjectProxyService._profile(self.project), status='READY',
            proxy_file=SimpleUploadedFile('clean-master.mp4', b'original-clock'),
        )
        self.addCleanup(proxy.proxy_file.delete, save=False)
        self.project.render_job.rendered_from_timeline_revision = 4
        self.project.render_job.save(update_fields=['rendered_from_timeline_revision'])
        self.assertEqual(ProjectProxyService.original_review_master(self.project).name, proxy.proxy_file.name)
        response = self.client.get(reverse('external_media_project_preview_master', args=[self.project.public_id]))
        self.assertEqual(response.status_code, 200)
        proxy.delete()
        self.assertIsNone(ProjectProxyService.original_review_master(self.project))


@skipUnless(shutil.which(settings.FFMPEG_BINARY), 'FFmpeg is required for the linked audio/video check')
class VideoMoveRenderTests(SimpleTestCase):
    def test_rendered_picture_and_sound_follow_the_same_reordered_intervals(self):
        import numpy as np
        with TemporaryDirectory() as directory:
            source, output = Path(directory) / 'source.mp4', Path(directory) / 'moved.mp4'
            command = [settings.FFMPEG_BINARY, '-v', 'error', '-y', '-filter_complex_threads', '1']
            for color, tone in [('red', 220), ('green', 440), ('blue', 660)]:
                command += ['-f', 'lavfi', '-i', f'color=c={color}:s=160x90:r=30:d=1',
                            '-f', 'lavfi', '-i', f'sine=frequency={tone}:sample_rate=48000:duration=1']
            command += ['-filter_complex', '[0:v][1:a][2:v][3:a][4:v][5:a]concat=n=3:v=1:a=1[v][a]',
                        '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(source)]
            subprocess.run(command, check=True, capture_output=True, timeout=30)
            VideoAssemblyService()._concat_group([source, source, source], output, intervals=[
                {'start_ms': 0, 'end_ms': 1000},
                {'start_ms': 2000, 'end_ms': 3000},
                {'start_ms': 1000, 'end_ms': 2000},
            ])
            for time, channel, tone in [(.4, 0, 220), (1.4, 2, 660), (2.4, 1, 440)]:
                frame = subprocess.run([
                    settings.FFMPEG_BINARY, '-v', 'error', '-ss', str(time), '-i', str(output),
                    '-frames:v', '1', '-vf', 'scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-',
                ], check=True, capture_output=True, timeout=15).stdout
                self.assertEqual(max(range(3), key=lambda index: frame[index]), channel)
                audio = subprocess.run([
                    settings.FFMPEG_BINARY, '-v', 'error', '-ss', str(time), '-i', str(output),
                    '-t', '0.2', '-vn', '-ar', '48000', '-ac', '1', '-f', 's16le', '-',
                ], check=True, capture_output=True, timeout=15).stdout
                samples = np.frombuffer(audio, dtype='<i2').astype(float)
                frequencies = np.fft.rfftfreq(len(samples), 1 / 48000)
                frequency = frequencies[np.argmax(abs(np.fft.rfft(samples)))]
                self.assertAlmostEqual(frequency, tone, delta=10)
