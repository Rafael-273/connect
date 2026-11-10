import json
import struct
import sys
import tempfile
import unittest
import wave
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.utils import timezone

from website.external_media.preview_artifacts import PreviewArtifactService, artifact_key, wav_peaks


class PreviewArtifactTests(unittest.TestCase):
    def setUp(self):
        self.artifact = SimpleNamespace(pk=1, project_id=2, lease='', status='PENDING', file='',
            source={'name': 'source.mp4', 'version': 'v1'}, options={}, kind='waveform', key='abc',
            requested_at=timezone.now(), save=Mock(), error='')
        self.storage = Mock()
        self.storage.save.return_value = 'saved/waveform.json'
        self.manager = Mock()
        self.manager.select_for_update.return_value = self.manager
        self.manager.filter.return_value = self.manager
        self.manager.get.return_value = self.artifact
        self.manager.first.return_value = self.artifact
        self.manager.get_or_create.return_value = (self.artifact, True)
        def update(**values):
            for key, value in values.items():
                setattr(self.artifact, key, value)
            return 1
        self.manager.update.side_effect = update
        model = SimpleNamespace(objects=self.manager)
        self.models = patch.dict(sys.modules, {'website.models.external_media': SimpleNamespace(
            ProjectPreviewArtifact=model, get_external_media_storage=lambda: self.storage)})
        self.models.start()
        self.addCleanup(self.models.stop)
        atomic = patch('website.external_media.preview_artifacts.transaction.atomic', side_effect=lambda: nullcontext())
        atomic.start()
        self.addCleanup(atomic.stop)

    @patch('website.external_media.preview_artifacts.transaction.on_commit', side_effect=lambda callback: callback())
    @patch.object(PreviewArtifactService, 'enqueue')
    def test_duplicate_requests_and_reloads_enqueue_once_then_reuse_saved_file(self, enqueue, commit):
        project, field = object(), SimpleNamespace(name='source.mp4')
        PreviewArtifactService.request(project, 'waveform', field, version='v1')
        self.manager.get_or_create.return_value = (self.artifact, False)
        PreviewArtifactService.request(project, 'waveform', field, version='v1')
        enqueue.assert_called_once()
        self.artifact.status, self.artifact.file = 'READY', 'cached.json'
        self.assertEqual(PreviewArtifactService.request(project, 'waveform', field, version='v1').file, 'cached.json')
        enqueue.assert_called_once()

    def test_versions_parameters_and_variants_have_distinct_cache_keys(self):
        source = {'name': 'source.mp4', 'version': 'v1'}
        original = artifact_key('noise_comparison', source, {'variant': 'original', 'strength': 'LIGHT'})
        self.assertNotEqual(original, artifact_key('noise_comparison', source, {'variant': 'treated', 'strength': 'LIGHT'}))
        self.assertNotEqual(original, artifact_key('noise_comparison', source, {'variant': 'original', 'strength': 'STRONG'}))
        self.assertNotEqual(original, artifact_key('noise_comparison', {**source, 'version': 'v2'}, {'variant': 'original', 'strength': 'LIGHT'}))
        self.assertEqual(original, artifact_key('noise_comparison', dict(reversed(list(source.items()))), {'strength': 'LIGHT', 'variant': 'original'}))

    @patch('website.external_media.preview_artifacts.render_artifact')
    def test_worker_skips_duplicate_or_obsolete_lease_without_rendering(self, render):
        self.artifact.lease = 'new'
        PreviewArtifactService.build(1, 'old')
        self.artifact.status = 'READY'
        PreviewArtifactService.build(1, 'new')
        render.assert_not_called()

    @patch('website.external_media.workspace.JobWorkspace')
    @patch('website.external_media.media_input.media_input_factory')
    @patch('website.external_media.preview_artifacts.render_artifact')
    def test_worker_persists_output_and_subsequent_delivery_does_no_work(self, render, media, workspace):
        self.artifact.lease = 'job'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'waveform.json'
            output.write_text(json.dumps({'durationMs': 1000, 'peaks': [.1, .2]}))
            render.return_value = output
            PreviewArtifactService.build(1, 'job')
            PreviewArtifactService.build(1, 'job')
        render.assert_called_once()
        self.storage.save.assert_called_once()
        self.assertEqual(self.artifact.status, 'READY')
        self.assertEqual(self.artifact.file, 'saved/waveform.json')

    def test_pcm_peaks_keep_exact_duration_and_normalized_peak_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audio.wav'
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(struct.pack('<hhhh', 0, 16384, -32768, 8192))
            result = wav_peaks(path, buckets=4)
        self.assertEqual(result['durationMs'], .5)
        self.assertEqual(result['peaks'], [0, .5, 1, .25])

    def test_pending_and_error_responses_are_not_cached_as_finished_media(self):
        for status in ('PENDING', 'PROCESSING', 'ERROR'):
            response = PreviewArtifactService.pending_response(SimpleNamespace(status=status))
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response['Cache-Control'], 'no-store')
            self.assertEqual(json.loads(response.content)['status'], status)
