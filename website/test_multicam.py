"""Multicamera contracts and real FFmpeg regression fixtures (no external AI)."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch

import numpy as np
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from website.external_media.multicam import AudioSynchronizer, MulticamService, MulticamRenderService
from website.external_media.canonical import SourceManifestBuilder
from website.external_media.preview import TimelineRevisionService
from website.external_media.ffmpeg_runner import FFmpegRunner
from website.models.external_media import ProjectBlockMedia
from website.test_editor_persistence import EditorFixture


def speech(seed=1, seconds=35):
    rng = np.random.default_rng(seed)
    size = seconds * 8000
    # Aperiodic speech-like noise with time-varying formants and syllable energy.
    envelope = np.repeat(rng.uniform(.01, .8, size // 800 + 1), 800)[:size]
    carrier = rng.normal(size=size)
    return np.convolve(carrier, [.3, .4, .3], mode='same') * envelope


def fixture():
    sources = [
        {'id': 'main', 'duration_ms': 40000, 'trim': {'start_ms': 0}, 'metadata': {}},
        {'id': 'side', 'duration_ms': 45000, 'trim': {'start_ms': 0}, 'metadata': {'multicam_alignment': {'status': 'ALIGNED', 'offset_ms': 2000, 'rate': 1., 'reference_source_id': 'main', 'validated_until_primary_ms': 40000}}},
    ]
    manifest = {'sources': sources, 'multicam_groups': [{'reference_source_id': 'main', 'source_ids': ['main', 'side']}]}
    clips = [{'asset_id': 'main', 'source_in_ms': 0, 'source_out_ms': 40000, 'timeline_in_ms': 0, 'timeline_out_ms': 40000}]
    return manifest, clips


class AudioSyncTests(SimpleTestCase):
    def test_offset_and_microphone_quality(self):
        original = speech()
        filtered = np.convolve(original, [.6, .3, .1], mode='same') * -.3
        filtered += np.random.default_rng(9).normal(scale=.007, size=len(filtered))
        secondary = np.r_[np.zeros(16000), filtered]
        result = AudioSynchronizer.estimate(original, secondary)
        self.assertEqual(result['status'], 'ALIGNED', result)
        self.assertAlmostEqual(result['offset_ms'], 2000, delta=30)
        self.assertGreater(result['confidence'], .55)

    def test_secondary_started_later_with_different_duration(self):
        original = speech()
        result = AudioSynchronizer.estimate(original, original[24000:-40000])
        self.assertEqual(result['status'], 'ALIGNED', result)
        self.assertAlmostEqual(result['offset_ms'], -3000, delta=30)

    def test_both_signals_have_initial_silence(self):
        original = np.r_[np.zeros(32000), speech()]
        result = AudioSynchronizer.estimate(original, np.r_[np.zeros(8000), original])
        self.assertEqual(result['status'], 'ALIGNED', result)
        self.assertAlmostEqual(result['offset_ms'], 1000, delta=30)

    def test_reject_silence_missing_audio_and_unrelated_recordings(self):
        for first, second in [(np.zeros(80000), np.zeros(90000)), ([], speech()), (speech(), speech(2))]:
            with self.subTest(length=len(first)):
                self.assertEqual(AudioSynchronizer.estimate(first, second)['status'], 'REVIEW')

    def test_reject_repeated_ambiguous_audio(self):
        repeated = np.tile(speech(seconds=5), 7)
        self.assertEqual(AudioSynchronizer.estimate(repeated, repeated)['status'], 'REVIEW')

    def test_small_clock_drift(self):
        primary = speech(seconds=90)
        ratio = 1.0005
        secondary = np.interp(np.arange(int(len(primary) * ratio)) / ratio, np.arange(len(primary)), primary)
        result = AudioSynchronizer.estimate(primary, secondary)
        self.assertEqual(result['status'], 'ALIGNED', result)
        self.assertAlmostEqual(result['rate'], ratio, delta=.00025)

    def test_excessive_drift_requires_review(self):
        primary = speech(seconds=90)
        ratio = 1.004
        secondary = np.interp(np.arange(int(len(primary) * ratio)) / ratio, np.arange(len(primary)), primary)
        self.assertEqual(AudioSynchronizer.estimate(primary, secondary)['status'], 'REVIEW')


class CameraDecisionTests(SimpleTestCase):
    def test_automatic_selection_limit_scales_with_duration(self):
        self.assertEqual(MulticamService.automatic_selection_limit({'sequence': {'duration_ms': 3 * 60_000}}), 2)
        self.assertEqual(MulticamService.automatic_selection_limit({'sequence': {'duration_ms': 25 * 60_000}}), 12)
        self.assertEqual(MulticamService.automatic_selection_limit({'sequence': {'duration_ms': 90 * 60_000}}), 18)
        self.assertEqual(MulticamService.automatic_selection_limit({'sequence': {'duration_ms': 25 * 60_000}}, 4), 4)

    def test_word_boundary_proposals_and_primary_preference(self):
        manifest, clips = fixture()
        words = [{'start_ms': 100, 'end_ms': 900, 'text': 'Mudou.'}, {'start_ms': 1300, 'end_ms': 1600, 'text': 'Mas'},
                 {'start_ms': 9000, 'end_ms': 9700, 'text': 'tempo.'}, {'start_ms': 10100, 'end_ms': 10400, 'text': 'Continuamos'}]
        project = SimpleNamespace(public_id='fa554b69-2fbc-413f-9860-223585b6fc9e', configuration={'multicam_words': words})
        operations = MulticamService.propose(project, manifest, {'clips': clips})
        self.assertEqual(len(operations), 1)
        self.assertEqual((operations[0]['source_in_ms'], operations[0]['source_out_ms']), (1100, 9900))
        # Segment timestamps alone never become precise cut points.
        project.configuration = {}
        self.assertEqual(MulticamService.propose(project, manifest, {'clips': clips}), [])

    def test_long_silences_create_safe_candidates_without_transcript_punctuation(self):
        manifest, clips = fixture()
        words = [
            {'start_ms': 0, 'end_ms': 800, 'text': 'primeiro'},
            {'start_ms': 1600, 'end_ms': 2200, 'text': 'pensamento'},
            {'start_ms': 16000, 'end_ms': 16600, 'text': 'continua'},
        ]
        project = SimpleNamespace(
            public_id='fa554b69-2fbc-413f-9860-223585b6fc9e',
            configuration={'multicam_words': words},
        )

        operations = MulticamService.propose(project, manifest, {'clips': clips})

        self.assertEqual(len(operations), 1)
        self.assertEqual((operations[0]['source_in_ms'], operations[0]['source_out_ms']), (1200, 9100))

    def test_low_confidence_requires_explicit_manual_offset(self):
        manifest, clips = fixture()
        manifest['sources'][1]['metadata']['multicam_alignment']['status'] = 'REVIEW'
        with self.assertRaisesMessage(ValueError, 'requer revisão'):
            MulticamService.selection(manifest, reference_id='main', camera_id='side', start=1000, end=5000)
        item = MulticamService.selection(manifest, reference_id='main', camera_id='side', start=1000, end=5000, offset=100)
        self.assertEqual(item['alignment']['status'], 'MANUAL')

    def test_camera_decisions_survive_manual_cuts_and_map_to_source(self):
        manifest, clips = fixture()
        operation = MulticamService.selection(manifest, reference_id='main', camera_id='side', start=4000, end=12000)
        # A manual speech cut removed 5000..8000, including camera picture.
        clips = [dict(clips[0], source_out_ms=5000, timeline_out_ms=5000),
                 dict(clips[0], source_in_ms=8000, timeline_in_ms=5000, timeline_out_ms=37000)]
        _, segments = MulticamService.compose(manifest, [operation], clips, 'fa554b69-2fbc-413f-9860-223585b6fc9e')
        self.assertEqual([(s['start_ms'], s['end_ms'], s['source_in_ms']) for s in segments], [(4000, 5000, 6000), (5000, 9000, 10000)])

    def test_reject_uncovered_and_overlapping_ranges(self):
        manifest, clips = fixture()
        operation = MulticamService.selection(manifest, reference_id='main', camera_id='side', start=1000, end=5000)
        other = dict(operation, id='other', source_in_ms=3000)
        with self.assertRaisesMessage(ValueError, 'sobrepor'):
            MulticamService.compose(manifest, [operation, other], clips, 'fa554b69-2fbc-413f-9860-223585b6fc9e')
        operation['alignment']['offset_ms'] = -2000
        with self.assertRaisesMessage(ValueError, 'não cobre'):
            MulticamService.compose(manifest, [operation], clips, 'fa554b69-2fbc-413f-9860-223585b6fc9e')

    def test_reject_other_group_and_nonfinite_manual_values(self):
        manifest, _ = fixture()
        for options in [{'camera_id': 'foreign'}, {'camera_id': 'side', 'offset': float('nan')}, {'camera_id': 'side', 'offset': 0, 'rate': 2}]:
            with self.assertRaises(ValueError):
                MulticamService.selection(manifest, reference_id='main', start=1000, end=5000, **options)

    def test_unvalidated_tail_requires_manual_confirmation(self):
        manifest, _ = fixture()
        manifest['sources'][1]['metadata']['multicam_alignment']['validated_until_primary_ms'] = 5000
        with self.assertRaisesMessage(ValueError, 'fora da região'):
            MulticamService.selection(manifest, reference_id='main', camera_id='side', start=6000, end=9000)
        item = MulticamService.selection(manifest, reference_id='main', camera_id='side', start=6000, end=9000, offset=2000)
        self.assertEqual(item['alignment']['status'], 'MANUAL')

    def test_null_alignment_and_wordless_transcripts_are_safe(self):
        manifest, clips = fixture()
        manifest['sources'][0]['metadata']['multicam_alignment'] = None
        project = SimpleNamespace(public_id='fa554b69-2fbc-413f-9860-223585b6fc9e', configuration={'multicam_words': [
            {'start_ms': 100, 'end_ms': 900, 'text': 'Muda.'}, {'start_ms': 1300, 'end_ms': 1600, 'text': 'Mas'},
            {'start_ms': 9000, 'end_ms': 9700, 'text': 'Fim.'}, {'start_ms': 10100, 'end_ms': 10400, 'text': 'Volta'}]})
        self.assertEqual(len(MulticamService.propose(project, manifest, {'clips': clips})), 1)


class CameraSemanticReviewTests(SimpleTestCase):
    def project(self, transition='Mas'):
        manifest, clips = fixture()
        return SimpleNamespace(public_id='fa554b69-2fbc-413f-9860-223585b6fc9e', save=Mock(), configuration={
            'multicam_alignments': {'side': manifest['sources'][1]['metadata']['multicam_alignment']},
            'source_manifest': manifest, 'multicam_words': [
                {'start_ms': 100, 'end_ms': 900, 'text': 'Mudou.'}, {'start_ms': 1300, 'end_ms': 1600, 'text': transition},
                {'start_ms': 9000, 'end_ms': 9700, 'text': 'tempo.'}, {'start_ms': 10100, 'end_ms': 10400, 'text': 'Continuamos'}]})

    def test_semantic_choice_uses_only_precise_pause_ids_and_is_cached(self):
        project = self.project('Hoje')
        model = Mock()
        model.generate_text.return_value = json.dumps({'selections': [
            {'pause_id': 'pause-1100', 'camera_id': 'side', 'confidence': .9, 'reason': 'Novo argumento'}]})
        review = MulticamService.review_candidates(project, model)
        self.assertEqual(review['status'], 'REVIEWED')
        manifest, clips = fixture()
        operations = MulticamService.propose(project, manifest, {'clips': clips})
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]['source_in_ms'], 1100)
        self.assertEqual(operations[0]['reason'], 'Novo argumento')
        self.assertEqual(operations[0]['origin'], 'AI')
        MulticamService.review_candidates(project, model)
        model.generate_text.assert_called_once()

    def test_empty_semantic_response_keeps_main_camera(self):
        project = self.project()
        MulticamService.review_candidates(project, Mock(generate_text=Mock(return_value='{"selections":[]}')))
        manifest, clips = fixture()
        self.assertEqual(MulticamService.propose(project, manifest, {'clips': clips}), [])

    def test_hallucinated_ids_and_weak_choices_never_become_cuts(self):
        project = self.project()
        choices = [
            {'pause_id': 'pause-1100', 'camera_id': 'foreign', 'confidence': .99},
            {'pause_id': 'invented-123', 'camera_id': 'side', 'confidence': .99},
            {'pause_id': 'pause-1100', 'camera_id': 'side', 'confidence': .4},
        ]
        review = MulticamService.review_candidates(project, Mock(generate_text=Mock(return_value=json.dumps({'selections': choices}))))
        self.assertEqual(review['selections'], [])

    def test_provider_failure_preserves_local_fallback(self):
        from website.ai import AIServiceError
        project = self.project()
        review = MulticamService.review_candidates(project, Mock(generate_text=Mock(side_effect=AIServiceError('Unavailable'))))
        self.assertEqual(review['status'], 'LOCAL_FALLBACK')
        manifest, clips = fixture()
        self.assertEqual(len(MulticamService.propose(project, manifest, {'clips': clips})), 1)


class CameraPersistenceTests(EditorFixture, TestCase):
    def setUp(self):
        super().setUp()
        main = self.project.block_media.first()
        self.side = ProjectBlockMedia.objects.create(project=self.project, block=main.block, position=1, camera_order=2,
            camera_role='SECONDARY', camera_key='side', duration_ms=10000, file='side.mp4', original_filename='side.mp4')
        manifest = SourceManifestBuilder.build(self.project)
        self.main_id = manifest['multicam_groups'][0]['reference_source_id']
        self.side_id = next(s['id'] for s in manifest['sources'] if s['source_reference'] == str(self.side.pk))
        self.project.current_timeline_revision.source_manifest = manifest
        self.project.current_timeline_revision.save(update_fields=['source_manifest'])

    def test_add_move_change_remove_camera_and_history(self):
        payload = {'camera_source_id': self.side_id, 'start_ms': 1000, 'end_ms': 5000, 'offset_ms': 200}
        revision = TimelineRevisionService.update_camera(self.project, self.member, payload)
        item = revision.timeline['camera_segments'][0]
        self.assertEqual(item['source_in_ms'], 1200)
        moved = TimelineRevisionService.update_camera(self.project, self.member, {**payload, 'id': item['id'], 'start_ms': 2000, 'end_ms': 6000})
        self.assertEqual(moved.timeline['camera_segments'][0]['source_in_ms'], 2200)
        removed = TimelineRevisionService.update_camera(self.project, self.member, {'id': item['id'], 'delete': True})
        self.assertEqual(removed.timeline['camera_segments'], [])
        self.assertEqual(moved.timeline['camera_segments'][0]['end_ms'], 6000)
        self.assertEqual(revision.timeline['sequence']['duration_ms'], 10000)

    def test_manual_speech_cut_preserves_camera_sync(self):
        revision = TimelineRevisionService.update_camera(self.project, self.member, {'camera_source_id': self.side_id, 'start_ms': 1000, 'end_ms': 8000, 'offset_ms': 0})
        revision = TimelineRevisionService.create_manual_cut(self.project, self.member, 3000, 5000)
        segments = revision.timeline['camera_segments']
        self.assertEqual([(s['start_ms'], s['end_ms'], s['source_in_ms']) for s in segments], [(1000, 3000, 1000), (3000, 6000, 5000)])

    def test_http_mutation_and_invalid_input_rollback(self):
        response = self.client.post(reverse('external_media_project_preview_camera', args=[self.project.public_id]), json.dumps({'camera_source_id': self.side_id, 'start_ms': 1000, 'end_ms': 2000, 'offset_ms': 0}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()['timeline']['camera_segments']), 1)
        count = self.project.timeline_revisions.count()
        response = self.client.post(reverse('external_media_project_preview_camera', args=[self.project.public_id]), json.dumps({'camera_source_id': self.side_id, 'start_ms': 0, 'end_ms': 20000, 'offset_ms': 0}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.project.timeline_revisions.count(), count)

    def test_direct_upload_groups_are_present(self):
        self.project.block_media.update(block=None)
        manifest = SourceManifestBuilder.build(self.project)
        self.assertEqual(len(manifest['multicam_groups']), 1)
        self.assertEqual(manifest['multicam_groups'][0]['reference_source_id'], self.main_id.replace('upload-', 'project-media-'))

    def test_change_to_another_secondary_camera(self):
        other = ProjectBlockMedia.objects.create(project=self.project, block=self.side.block, position=1, camera_order=3,
            camera_role='SECONDARY', camera_key='other', duration_ms=10000, file='other.mp4', original_filename='other.mp4')
        current = self.project.current_timeline_revision
        current.source_manifest = SourceManifestBuilder.build(self.project)
        current.save(update_fields=['source_manifest'])
        other_id = next(s['id'] for s in current.source_manifest['sources'] if s['source_reference'] == str(other.pk))
        payload = {'camera_source_id': self.side_id, 'start_ms': 1000, 'end_ms': 4000, 'offset_ms': 0}
        initial = TimelineRevisionService.update_camera(self.project, self.member, payload)
        changed = TimelineRevisionService.update_camera(self.project, self.member,
            {**payload, 'id': initial.timeline['camera_segments'][0]['id'], 'camera_source_id': other_id})
        self.assertEqual(changed.timeline['camera_segments'][0]['camera_source_id'], other_id)
        self.assertEqual(initial.timeline['camera_segments'][0]['camera_source_id'], self.side_id)

    def test_adjust_camera_interval_across_a_blade_and_removed_speech(self):
        initial = TimelineRevisionService.update_camera(self.project, self.member,
            {'camera_source_id': self.side_id, 'start_ms': 1000, 'end_ms': 8000, 'offset_ms': 0})
        selection_id = initial.timeline['camera_segments'][0]['id']
        TimelineRevisionService.create_video_split(self.project, self.member, 6000)
        TimelineRevisionService.create_manual_cut(self.project, self.member, 3000, 5000)
        changed = TimelineRevisionService.update_camera(self.project, self.member,
            {'id': selection_id, 'camera_source_id': self.side_id, 'start_ms': 2000, 'end_ms': 6000, 'offset_ms': 0})
        self.assertEqual([(s['start_ms'], s['end_ms'], s['source_in_ms']) for s in changed.timeline['camera_segments']],
            [(2000, 3000, 2000), (3000, 4000, 5000), (4000, 6000, 6000)])

    def test_primary_trim_ranges_stay_in_one_group(self):
        main = self.project.block_media.filter(camera_role='PRIMARY').first()
        main.trim_ranges = [{'start_ms': 0, 'end_ms': 3000}, {'start_ms': 4000, 'end_ms': 10000}]
        main.save(update_fields=['trim_ranges'])
        manifest = SourceManifestBuilder.build(self.project)
        self.assertEqual(len(manifest['multicam_groups'][0]['reference_source_ids']), 2)
        current = self.project.current_timeline_revision
        TimelineRevisionService._create(self.project, manifest, current.edit_decision_set,
            'Trechos de teste', self.member, parent=current)
        revision = TimelineRevisionService.update_camera(self.project, self.member,
            {'camera_source_id': self.side_id, 'start_ms': 4000, 'end_ms': 7000, 'offset_ms': 0})
        self.assertEqual(revision.timeline['camera_segments'][0]['source_in_ms'], 5000)

    def test_cached_alignment_reused_and_replaced_source_invalidates_it(self):
        manifest = SourceManifestBuilder.build(self.project)
        reference = next(s for s in manifest['sources'] if s['id'] == self.main_id)
        side = next(s for s in manifest['sources'] if s['id'] == self.side_id)
        cached = {'algorithm_version': AudioSynchronizer.VERSION, 'status': 'ALIGNED', 'offset_ms': 100, 'rate': 1.,
                  'reference_source_id': self.main_id, 'storage_name': side['storage_name'], 'reference_storage_name': reference['storage_name']}
        self.project.configuration = {**self.project.configuration, 'multicam_alignments': {self.side_id: cached}}
        with patch.object(AudioSynchronizer, 'extract', side_effect=AssertionError('Cache must avoid extraction')):
            result = MulticamService.prepare(self.project, None, None, Path('/tmp'))
        self.assertEqual(result[self.side_id]['offset_ms'], 100)
        self.side.file.name = 'replaced.mp4'
        self.side.save(update_fields=['file'])
        from website.external_media.exceptions import ExternalMediaError
        with patch.object(AudioSynchronizer, 'extract', side_effect=ExternalMediaError('No audio')) as extract:
            result = MulticamService.prepare(self.project, SimpleNamespace(ffmpeg_input=lambda field: field), None, Path('/tmp'))
        self.assertEqual(result[self.side_id]['status'], 'REVIEW')
        extract.assert_called_once()


class MulticamFFmpegTests(SimpleTestCase):
    def run_media(self, *args):
        return subprocess.run([settings.FFMPEG_BINARY, '-v', 'error', '-y', *map(str, args)], check=True, capture_output=True).stdout

    def test_different_fps_picture_changes_audio_identical_and_shared_renderer(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            main, side = folder / 'main.mp4', folder / 'side.mp4'
            self.run_media('-f', 'lavfi', '-i', 'color=red:s=160x120:r=30:d=4', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', main)
            self.run_media('-f', 'lavfi', '-i', 'color=blue:s=160x120:r=24:d=5', '-f', 'lavfi', '-i', 'sine=frequency=880:duration=5', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', side)
            manifest, clips = fixture()
            selection = MulticamService.selection(manifest, reference_id='main', camera_id='side', start=1000, end=3000, offset=500)
            _, segments = MulticamService.compose(manifest, [selection], clips, 'fa554b69-2fbc-413f-9860-223585b6fc9e')
            revision = SimpleNamespace(timeline={'camera_segments': segments}, source_manifest=manifest)
            renderer = MulticamRenderService(FFmpegRunner(), SimpleNamespace(ffmpeg_input=lambda field: side))
            output = folder / 'output.mp4'
            with patch.object(SourceManifestBuilder, 'resolve_field', return_value=side):
                renderer.apply(SimpleNamespace(), revision, main, output, 160, 120)
            original_audio = self.run_media('-i', main, '-vn', '-c:a', 'copy', '-f', 'adts', '-')
            final_audio = self.run_media('-i', output, '-vn', '-c:a', 'copy', '-f', 'adts', '-')
            self.assertEqual(original_audio, final_audio)
            for moment, expected in [(.5, 'red'), (1.5, 'blue'), (3.5, 'red')]:
                frame = self.run_media('-ss', moment, '-i', output, '-frames:v', 1, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-')
                rgb = np.frombuffer(frame, np.uint8).reshape(-1, 3).mean(axis=0)
                self.assertGreater(rgb[0 if expected == 'red' else 2], 200, (moment, rgb))
            # Both integration points must execute this single compositor.
            import inspect
            from website.external_media.preview import ProjectProxyService
            from website.external_media.services import ExternalMediaProjectPipeline
            self.assertIn('MulticamRenderService', inspect.getsource(ProjectProxyService.build_timeline_review_proxy))
            self.assertIn('MulticamRenderService', inspect.getsource(ExternalMediaProjectPipeline._prepare_final_master))

    def test_audio_extraction_resamples_and_missing_audio_fails_safely(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            audio, silent_video = folder / 'audio.wav', folder / 'silent.mp4'
            self.run_media('-f', 'lavfi', '-i', 'sine=frequency=500:sample_rate=44100:duration=6', audio)
            samples = AudioSynchronizer.extract(audio, folder / 'sync.wav', FFmpegRunner())
            self.assertEqual(len(samples), 48000)
            self.run_media('-f', 'lavfi', '-i', 'color=black:s=160x120:d=1', silent_video)
            from website.external_media.exceptions import ExternalMediaError
            with self.assertRaises(ExternalMediaError):
                AudioSynchronizer.extract(silent_video, folder / 'missing.wav', FFmpegRunner())

class CameraEndToEndTests(EditorFixture, TestCase):
    def test_approved_preview_and_final_render_have_identical_camera_boundaries(self):
        from django.core.files import File
        from website.external_media.preview import ProjectProxyService
        from website.external_media.services import ExternalMediaProjectPipeline
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            runner = FFmpegRunner()
            for color, rate, frequency in [('red', 30, 440), ('blue', 24, 880)]:
                runner.run([settings.FFMPEG_BINARY, '-y', '-f', 'lavfi', '-i', f'color={color}:s=160x120:r={rate}:d=10',
                            '-f', 'lavfi', '-i', f'sine=frequency={frequency}:duration=10',
                            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(directory / f'{color}.mp4')])
            self.preset.width, self.preset.height = 160, 120
            self.preset.save(update_fields=['width', 'height'])
            version = self.project.template_version
            version.subtitles_enabled = False
            version.save(update_fields=['subtitles_enabled'])
            main = self.project.block_media.first()
            with (directory / 'red.mp4').open('rb') as handle:
                main.file.save('main.mp4', File(handle))
            with (directory / 'red.mp4').open('rb') as handle:
                self.project.render_job.original_video.save('review-source.mp4', File(handle))
            side = ProjectBlockMedia.objects.create(project=self.project, block=main.block, position=1, camera_order=2,
                camera_role='SECONDARY', camera_key='side', duration_ms=10000, original_filename='side.mp4')
            with (directory / 'blue.mp4').open('rb') as handle:
                side.file.save('side.mp4', File(handle))
            ProjectProxyService.prepare(self.project)
            manifest = SourceManifestBuilder.build(self.project)
            current = self.project.current_timeline_revision
            current.source_manifest = manifest
            current.save(update_fields=['source_manifest'])
            side_id = next(item['id'] for item in manifest['sources'] if item['source_reference'] == str(side.pk))
            revision = TimelineRevisionService.update_camera(self.project, self.member, {
                'camera_source_id': side_id, 'start_ms': 1000, 'end_ms': 5000, 'offset_ms': 0})
            preview = ProjectProxyService.build_timeline_review_proxy(self.project.pk, revision.pk)
            self.assertEqual(preview.status, 'READY')
            self.assertIn('MULTICAM', preview.metadata['rendered_layers'])
            self.project.refresh_from_db()
            self.project.approved_timeline_revision = revision
            self.project.configuration = {**self.project.configuration,
                'analysis_source_duration_ms': 10000,
                'analysis_source_block_ranges': [{'block_key': 'video', 'start_ms': 0, 'end_ms': 10000}]}
            self.project.save(update_fields=['approved_timeline_revision', 'configuration'])
            ExternalMediaProjectPipeline()._prepare_final_master(self.project, directory)
            self.project.render_job.refresh_from_db()
            delivery = self.project.render_job.original_video
            self.assertTrue(self.project.configuration['quality_report']['ok'])
            for path in (preview.proxy_file.path, delivery.path):
                info = subprocess.check_output([settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=r_frame_rate,avg_frame_rate,nb_frames,duration,start_time', '-of', 'json', path]).decode()
                self.assertEqual(json.loads(info)['streams'][0]['r_frame_rate'], '30/1')
                for frame_number, channel in [(15, 0), (30, 2), (149, 2), (150, 0), (180, 0)]:
                    raw = subprocess.check_output([settings.FFMPEG_BINARY, '-v', 'error', '-i', path,
                        '-vf', f'select=eq(n\\,{frame_number})', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
                    rgb = np.frombuffer(raw, np.uint8).reshape(-1, 3).mean(axis=0)
                    self.assertGreater(rgb[channel], 200, (path, frame_number, rgb))
                audio = subprocess.check_output([settings.FFMPEG_BINARY, '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'])
                samples = np.frombuffer(audio, '<f4')
                for second in (.5, 2., 6.):
                    window = samples[int(second * 8000):int((second + .25) * 8000)]
                    peak = np.argmax(abs(np.fft.rfft(window))) * 8000 / len(window)
                    self.assertAlmostEqual(peak, 440, delta=5)
            # A finished delivery already contains blue camera pictures. A new
            # review removing that camera must use the cached clean main source.
            removed = TimelineRevisionService.update_camera(self.project, self.member,
                {'id': revision.timeline['camera_segments'][0]['id'], 'delete': True})
            restored = ProjectProxyService.build_timeline_review_proxy(self.project.pk, removed.pk)
            raw = subprocess.check_output([settings.FFMPEG_BINARY, '-v', 'error', '-ss', '2', '-i', restored.proxy_file.path,
                '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
            self.assertGreater(np.frombuffer(raw, np.uint8).reshape(-1, 3).mean(axis=0)[0], 200)



@skipUnless(os.environ.get('FILADELFIA_PLAYWRIGHT_MODULE'), 'Set FILADELFIA_PLAYWRIGHT_MODULE for full editor browser checks')
class CameraEditorBrowserTests(EditorFixture, StaticLiveServerTestCase):
    def test_camera_controls_on_real_editor(self):
        main = self.project.block_media.first()
        ProjectBlockMedia.objects.create(project=self.project, block=main.block, position=1, camera_order=2,
            camera_role='SECONDARY', camera_key='side', duration_ms=10000, file='side.mp4', original_filename='side.mp4')
        current = self.project.current_timeline_revision
        TimelineRevisionService._create(self.project, SourceManifestBuilder.build(self.project), current.edit_decision_set,
            'Câmeras de teste', self.member, parent=current)
        from website.test_editor_browser import EditorBrowserTests
        EditorBrowserTests.browser_check(self, 'editor_multicam_integration.browser.cjs')
