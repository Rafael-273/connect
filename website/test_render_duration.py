import json
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from website.external_media.quality_control import MediaQualityService
from website.external_media.multicam import MulticamRenderService
from website.external_media.exceptions import ExternalMediaError


class RenderDurationTests(SimpleTestCase):
    def test_bad_duration_skips_expensive_audio_analysis(self):
        runner = Mock()
        runner.run.return_value = json.dumps({
            'format': {'duration': '608'},
            'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}],
        })
        with patch('website.external_media.quality_control.AudioAnalysisService.analyze') as analyze:
            report = MediaQualityService(runner).validate_media(Path('master.mp4'), expected_duration_ms=4000, deep_audio=True)
        self.assertFalse(report.ok)
        self.assertIn('604.00s', report.errors[0])
        self.assertIn('esperado: 4.00s; obtido: 608.00s', report.errors[0])
        analyze.assert_not_called()
        self.assertEqual(runner.run.call_count, 1)

    def test_camera_rate_must_be_finite_and_positive(self):
        for rate in (0, -1, float('nan'), float('inf')):
            with self.subTest(rate=rate), self.assertRaises(ExternalMediaError):
                MulticamRenderService.validate_timing([{
                    'start_ms': 0, 'end_ms': 1000, 'source_in_ms': 0,
                    'source_out_ms': 1000, 'rate': rate,
                }], 2000)

    def test_invalid_master_stops_before_audio_and_multicam(self):
        from types import SimpleNamespace
        from tempfile import TemporaryDirectory
        from website.external_media.services import ExternalMediaProjectPipeline, AssemblySource
        from website.external_media.quality_control import QualityReport
        revision = SimpleNamespace(pk=4, revision=2, edit_decision_set={'operations': []},
            source_manifest={'sources': [{'id': 'a', 'duration_ms': 1000}]},
            timeline={'clips': [{'asset_id': 'a', 'source_in_ms': 0, 'source_out_ms': 1000}]})
        ranges = [{'start_ms': 0, 'end_ms': 1000, 'block_key': 'a'}]
        project = SimpleNamespace(public_id='test', approved_timeline_revision=revision,
            current_timeline_revision=revision, render_job=SimpleNamespace(pk=1),
            configuration={'analysis_source_duration_ms': 1000, 'analysis_source_block_ranges': ranges},
            template_version=SimpleNamespace(preset=SimpleNamespace(width=160, height=90),
                lut_intensity=0, music_volume=0, audio_mastering_enabled=True, dialogue_processing_enabled=False, audio_noise_cleanup_enabled=False))
        pipeline = ExternalMediaProjectPipeline()
        pipeline.templates = Mock()
        pipeline.templates.enabled_plugins.return_value = []
        pipeline.assembly = Mock(last_block_ranges=ranges)
        pipeline.quality = Mock()
        pipeline.quality.validate_media.return_value = QualityReport(errors=['Duração inválida'])
        with TemporaryDirectory() as directory, patch.object(pipeline, '_materialize',
                return_value=([AssemblySource(Path('a.mp4'), label='a')], None, None)), \
                patch.object(pipeline, '_update'), patch.object(pipeline, '_finalize_audio') as audio, \
                patch.object(MulticamRenderService, 'apply') as multicam:
            with self.assertRaises(ExternalMediaError):
                pipeline._prepare_final_master(project, Path(directory))
        audio.assert_not_called()
        multicam.assert_not_called()

    def test_invalid_camera_stops_before_materialization(self):
        from types import SimpleNamespace
        from website.external_media.services import ExternalMediaProjectPipeline
        revision = SimpleNamespace(timeline={'sequence': {'duration_ms': 1000}, 'camera_segments': [
            {'start_ms': 0, 'end_ms': 600000},
        ]})
        project = SimpleNamespace(approved_timeline_revision=revision, current_timeline_revision=revision)
        pipeline = ExternalMediaProjectPipeline()
        with patch.object(pipeline, '_materialize') as materialize, self.assertRaises(ExternalMediaError):
            pipeline._prepare_final_master(project, Path('/tmp'))
        materialize.assert_not_called()
