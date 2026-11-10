import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from website.external_media.services import AssemblySource, ExternalMediaProjectPipeline, VideoAssemblyService


class ApprovedFramingTests(SimpleTestCase):
    def revision(self):
        plan = {'analysis_width': 480, 'analysis_height': 270, 'plan': {
            'crop_width': 480, 'crop_height': 150,
            'keyframes': [{'time_seconds': 0, 'x': 0, 'y': 0}],
        }}
        return SimpleNamespace(
            source_manifest={'sources': [{'id': 'take', 'duration_ms': 3000}]},
            timeline={'clips': [
                {'asset_id': 'take', 'source_in_ms': start, 'source_out_ms': start + 1000,
                 'master_in_ms': start, 'master_out_ms': start + 1000}
                for start in (0, 1000, 2000)
            ]},
            edit_decision_set={'operations': [
                {'id': 'auto', 'type': 'reframe', 'source_id': 'take', 'plan_reference': plan, 'metadata': {}},
                {'id': 'manual', 'type': 'reframe', 'source_id': 'take', 'metadata': {
                    'segment_start_ms': 0, 'segment_end_ms': 1000,
                    'manual_transform': {'scale': 1, 'x': 0, 'y': -1},
                }},
                {'id': 'original', 'type': 'reframe', 'source_id': 'take', 'enabled': False,
                 'metadata': {'segment_start_ms': 2000, 'segment_end_ms': 3000}},
            ]},
        )

    def intervals(self, revision=None):
        return ExternalMediaProjectPipeline._approved_source_intervals(
            revision or self.revision(), [AssemblySource(Path('take.mp4'), label='take')],
        )

    def test_manual_auto_and_original_cuts_keep_their_own_framing(self):
        intervals = self.intervals()
        self.assertEqual(intervals[0]['manual_transform'], {'scale': 1, 'x': 0, 'y': -1})
        self.assertIsNone(intervals[0]['auto_reframe_plan'])
        self.assertEqual(intervals[1]['auto_reframe_plan']['plan']['crop_height'], 150)
        self.assertIsNone(intervals[1]['manual_transform'])
        self.assertTrue(intervals[2]['restore_original'])
        self.assertIsNone(intervals[2]['auto_reframe_plan'])

    def test_source_wide_manual_adjustment_is_applied_to_delivery_intervals(self):
        revision = self.revision()
        transform = {'scale': 1.35, 'x': .2, 'y': .8}
        revision.edit_decision_set['operations'] = [
            {'type': 'reframe', 'source_id': 'take', 'metadata': {'manual_transform': transform}},
        ]
        self.assertTrue(all(item['manual_transform'] == transform for item in self.intervals(revision)))

    def test_legacy_manual_effect_shown_in_approved_clip_is_not_lost_at_render(self):
        revision = self.revision()
        revision.edit_decision_set['operations'] = revision.edit_decision_set['operations'][:1]
        revision.timeline['clips'][1]['effects'] = [{'type': 'transform', 'scale': 1.2, 'x': .1, 'y': 1}]
        intervals = self.intervals(revision)
        self.assertEqual(intervals[1]['manual_transform'], {'scale': 1.2, 'x': .1, 'y': 1})
        self.assertIsNone(intervals[1]['auto_reframe_plan'])
        self.assertTrue(intervals[0]['auto_reframe_plan'])

    def test_manual_at_3m42s_uses_snapshot_master_coordinates_even_with_legacy_manifest_durations(self):
        revision = self.revision()
        revision.source_manifest['sources'].insert(0, {'id': 'previous-without-duration'})
        revision.timeline['clips'] = [{
            'asset_id': 'take', 'source_in_ms': 1000, 'source_out_ms': 2000,
            'master_in_ms': 222000, 'master_out_ms': 223000,
        }]
        manual = revision.edit_decision_set['operations'][1]
        manual['metadata'].update(segment_start_ms=222000, segment_end_ms=223000)
        self.assertEqual(self.intervals(revision)[0]['manual_transform'], manual['metadata']['manual_transform'])

    def test_restore_auto_uses_the_original_plan_without_copying_manual_framing(self):
        revision = self.revision()
        revision.edit_decision_set['operations'][1]['metadata'].pop('manual_transform')
        self.assertEqual(self.intervals(revision)[0]['auto_reframe_plan'], revision.edit_decision_set['operations'][0]['plan_reference'])

    @override_settings(USE_S3=False, EXTERNAL_MEDIA_ASSEMBLY_WORKERS=1)
    def test_automatic_curve_is_rebased_to_the_cut_source_clock_before_envelope_reuse(self):
        revision = self.revision()
        plan = revision.edit_decision_set['operations'][0]['plan_reference']['plan']
        plan['keyframes'].append({'time_seconds': 3, 'x': 0, 'y': 100})
        service = VideoAssemblyService(runner=Mock())
        with TemporaryDirectory() as directory, patch.object(service, '_effective_duration_ms', return_value=3000), \
                patch.object(service, '_video_dimensions', return_value=(480, 270)), \
                patch.object(service, '_normalize', return_value=None), patch.object(service, '_concat_normalized') as concat:
            service.assemble(
                [AssemblySource(Path('take.mp4'), label='take')], Path(directory) / 'out.mp4',
                SimpleNamespace(width=480, height=150), Path(directory), source_intervals=self.intervals(revision),
                auto_reframe_config={'priority': 'face'}, reframe_plans=[{'disabled': True}],
            )
        automatic = concat.call_args.kwargs['intervals'][1]['concat_auto_reframe']
        self.assertEqual([item['time_seconds'] for item in automatic['keyframes']], [-1, 2])


@skipUnless(shutil.which(settings.FFMPEG_BINARY), 'FFmpeg is required for frame comparison')
@override_settings(USE_S3=False, EXTERNAL_MEDIA_ASSEMBLY_WORKERS=1)
class RenderedFramingTests(SimpleTestCase):
    def test_decoded_frames_preserve_manual_position_and_automatic_neighbor_from_the_same_upload(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / 'take.mp4', root / 'rendered.mp4'
            subprocess.run([
                settings.FFMPEG_BINARY, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'color=c=green:s=480x270:r=30:d=3,drawbox=x=0:y=0:w=480:h=60:color=red:t=fill,drawbox=x=0:y=210:w=480:h=60:color=blue:t=fill',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=3',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', str(source),
            ], check=True, capture_output=True, timeout=30)
            fixture = ApprovedFramingTests()
            intervals = fixture.intervals()
            service = VideoAssemblyService()
            service.assemble(
                [AssemblySource(source, label='take')], output, SimpleNamespace(width=480, height=150), root,
                source_intervals=intervals, auto_reframe_config={'priority': 'face'}, reframe_plans=[{'disabled': True}],
            )
            for time, row, expected_channel in [(.5, 140, 2), (1.5, 5, 0), (2.5, 5, 1)]:
                pixels = subprocess.run([
                    settings.FFMPEG_BINARY, '-v', 'error', '-ss', str(time), '-i', str(output),
                    '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-',
                ], check=True, capture_output=True, timeout=15).stdout
                pixel = pixels[(row * 480 + 240) * 3:(row * 480 + 240) * 3 + 3]
                self.assertEqual(max(range(3), key=lambda index: pixel[index]), expected_channel,
                                 f'wrong framing at {time}s, pixel={list(pixel)}')
