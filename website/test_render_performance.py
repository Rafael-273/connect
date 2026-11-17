"""Local FFmpeg regressions for full-quality render optimizations."""
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from django.conf import settings
from django.core.files.storage import storages
from django.test import SimpleTestCase, override_settings

from website.external_media.ffmpeg_runner import FFmpegRunner
from website.external_media.services import VideoAssemblyService
from website.external_media.render_cache import RenderCheckpointCache, PREFIX
from website.external_media.audio_noise import (
    AudioCleanupService, NoiseReductionDecision, ReductionMode, ReductionStrength,
    NoiseType, RecommendedAction,
)


class RecordingRunner(FFmpegRunner):
    def __init__(self):
        self.commands = []

    def run_capture(self, command, **kwargs):
        self.commands.append(command)
        return super().run_capture(command, **kwargs)


@override_settings(EXTERNAL_MEDIA_WORKSPACE_MIN_FREE_GB=0)
class RenderPerformanceTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        storage_config = {**settings.STORAGES, 'external_media': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
            'OPTIONS': {'location': str(self.root / 'cache')},
        }}
        override = override_settings(STORAGES=storage_config, USE_S3=False,
                                     EXTERNAL_MEDIA_RENDER_CACHE_ENABLED=True)
        override.enable()
        self.addCleanup(override.disable)
        self.runner = RecordingRunner()
        self.source = self.root / 'source.mp4'
        self.runner.run([settings.FFMPEG_BINARY, '-y', '-f', 'lavfi', '-i', 'testsrc2=s=160x120:r=30:d=8',
                         '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8', '-c:v', 'libx264',
                         '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-shortest', str(self.source)])

    def decode(self, path, audio=False):
        args = ['-vn', '-f', 'f32le', '-acodec', 'pcm_f32le'] if audio else ['-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24']
        return subprocess.run([settings.FFMPEG_BINARY, '-v', 'error', '-i', str(path), *args, '-'],
                              capture_output=True, check=True).stdout

    def test_seek_preserves_frames_and_audio_for_late_reordered_cuts(self):
        service = VideoAssemblyService(runner=self.runner)
        intervals = [{'start_ms': 7000, 'end_ms': 7800}, {'start_ms': 5000, 'end_ms': 6000}]
        old, new = self.root / 'old.mp4', self.root / 'new.mp4'
        service._concat_group_uncached([self.source] * 2, old, intervals, seek_inputs=False)
        service._concat_group_uncached([self.source] * 2, new, intervals)
        self.assertEqual(self.decode(old), self.decode(new))
        left, right = np.frombuffer(self.decode(old, True), '<f4'), np.frombuffer(self.decode(new, True), '<f4')
        self.assertEqual(len(left), len(right))
        np.testing.assert_allclose(left, right, atol=1e-6)
        self.assertTrue(any('-accurate_seek' in command for command in self.runner.commands))

    def test_normalization_checkpoint_skips_encoding_and_rejects_corruption(self):
        service = VideoAssemblyService(runner=self.runner)
        output = self.root / 'normalized.mp4'
        recipe = dict(source=self.source, destination=output, width=160, height=120, lut_path=None)
        service._normalize(**recipe)
        original = output.read_bytes()
        output.unlink()
        self.runner.commands.clear()
        service._normalize(**recipe)
        self.assertEqual(output.read_bytes(), original)
        self.assertFalse(any('libx264' in command for command in self.runner.commands))
        key = service._render_checkpoints().keys[str(output)]
        storage = storages['external_media']
        with storage.open(f'{PREFIX}/{key}.json') as source:
            metadata = json.load(source)
        Path(storage.path(metadata['name'])).write_bytes(b'corrupt')
        output.unlink()
        self.runner.commands.clear()
        service._normalize(**recipe)
        self.assertTrue(any('libx264' in command for command in self.runner.commands))
        self.assertEqual(output.read_bytes(), original)

    def test_concat_checkpoint_reuses_complete_group(self):
        service = VideoAssemblyService(runner=self.runner)
        output = self.root / 'concat.mp4'
        intervals = [{'start_ms': 1000, 'end_ms': 2000}]
        service._concat_group([self.source], output, intervals)
        original = output.read_bytes()
        output.unlink()
        self.runner.commands.clear()
        service._concat_group([self.source], output, intervals)
        self.assertEqual(original, output.read_bytes())
        self.assertFalse(any('libx264' in command for command in self.runner.commands))

    def test_recipe_changes_and_source_content_invalidate_cache(self):
        cache = RenderCheckpointCache(self.runner)
        first = cache.key('test', {'source': cache.identity(self.source), 'trim': 0})
        self.assertNotEqual(first, cache.key('test', {'source': cache.identity(self.source), 'trim': 1000}))
        self.source.write_bytes(b'changed')
        self.assertNotEqual(first, cache.key('test', {'source': cache.identity(self.source), 'trim': 0}))

    def test_noise_cleanup_muxes_video_once_and_removes_owned_buffers(self):
        service = AudioCleanupService(runner=self.runner)
        decisions = [NoiseReductionDecision(start, start + 500, ReductionMode.LOCAL,
            ReductionStrength.LIGHT, 'test', NoiseType.TRANSIENT_NOISE, True,
            RecommendedAction.APPLY, False, .9, 'test', None) for start in (1000, 1300, 5000)]
        self.runner.commands.clear()
        output = self.root / 'cleaned.mp4'
        result = service.apply(self.source, output, decisions)
        self.assertEqual(result.metrics['applied'], 3)
        self.assertEqual(result.path, output)
        self.assertEqual(self.decode(self.source), self.decode(output))
        self.assertFalse(list(self.root.glob('noise_*.wav')))
        muxes = [command for command in self.runner.commands if '-c:v' in command and command[command.index('-c:v') + 1] == 'copy']
        self.assertEqual(len(muxes), 1)
        self.assertEqual(len(self.decode(output, True)), len(self.decode(self.source, True)))

    def test_static_sixty_fps_normalization_keeps_identical_frames(self):
        self.runner.run([settings.FFMPEG_BINARY, '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x240:r=60:d=3',
                         '-f', 'lavfi', '-i', 'sine=duration=3', '-c:v', 'libx264', '-c:a', 'aac',
                         '-ar', '48000', '-ac', '2', '-shortest', str(self.source)])
        service = VideoAssemblyService(runner=self.runner)
        old, new = self.root / 'static-old.mp4', self.root / 'static-new.mp4'
        recipe = dict(source=self.source, width=160, height=120, lut_path=None,
                      color_adjustment={'brightness': 10, 'contrast': 110, 'saturation': 80})
        service._normalize_uncached(destination=old, early_fps=False, **recipe)
        service._normalize_uncached(destination=new, **recipe)
        self.assertEqual(self.decode(old), self.decode(new))
        self.assertEqual(self.decode(old, True), self.decode(new, True))

    def test_expired_checkpoint_is_rebuilt_and_renewed(self):
        service = VideoAssemblyService(runner=self.runner)
        output = self.root / 'expired.mp4'
        intervals = [{'start_ms': 1000, 'end_ms': 2000}]
        service._concat_group([self.source], output, intervals)
        output.unlink()
        self.runner.commands.clear()
        with override_settings(EXTERNAL_MEDIA_RENDER_CACHE_TTL_SECONDS=1), patch('website.external_media.render_cache.time.time', return_value=0):
            # A fixed positive current time is not required for writing the fixture.
            key = service._render_checkpoints().keys[str(output)]
            storage = storages['external_media']
            manifest = Path(storage.path(f'{PREFIX}/{key}.json'))
            metadata = json.loads(manifest.read_text());metadata['created'] = -10
            manifest.write_text(json.dumps(metadata))
            service._concat_group([self.source], output, intervals)
        self.assertTrue(any('libx264' in command for command in self.runner.commands))

    def test_cleanup_is_dry_run_and_only_removes_expired_cache_files(self):
        import os
        from io import StringIO
        from django.core.management import call_command
        from django.core.files.base import ContentFile
        service = VideoAssemblyService(runner=self.runner)
        service._concat_group([self.source], self.root / 'cached.mp4', [{'start_ms': 0, 'end_ms': 1000}])
        storage = storages['external_media']
        storage.save('user/original.mp4', ContentFile(b'original'))
        cache_files = list((self.root / 'cache' / PREFIX).rglob('*'))
        for path in cache_files:
            if path.is_file():
                os.utime(path, (0, 0))
        call_command('cleanup_render_checkpoints', stdout=StringIO())
        self.assertTrue(any(path.exists() for path in cache_files if path.is_file()))
        from website.external_media.tasks import cleanup_render_checkpoints
        cleanup_render_checkpoints.run()
        self.assertFalse(any(path.is_file() for path in (self.root / 'cache' / PREFIX).rglob('*')))
        self.assertEqual(Path(storage.path('user/original.mp4')).read_bytes(), b'original')

    def test_remote_object_etag_changes_cache_identity_without_signed_url(self):
        from unittest.mock import Mock
        from website.external_media.media_input import RemoteMediaSource
        cache = RenderCheckpointCache(self.runner)
        storage = Mock(location='media', bucket_name='test')
        storage.connection.meta.client.head_object.side_effect = [
            {'ETag': 'one', 'ContentLength': 100}, {'ETag': 'two', 'ContentLength': 100}]
        source = RemoteMediaSource('https://example.invalid/clip?secret=token', storage_name='user/source.mov')
        with patch('website.external_media.render_cache.storages', {'external_media': storage}):
            first, second = cache.identity(source), cache.identity(source)
        self.assertNotEqual(first, second)
        self.assertNotIn('secret', json.dumps(first))

    def test_early_sampling_preserves_vfr_and_hdr_frames(self):
        service = VideoAssemblyService(runner=self.runner)
        for kind in ('vfr', 'hdr'):
            with self.subTest(kind=kind):
                source = self.root / f'{kind}.mp4'
                video = ['-vf', "select='not(mod(n,3))+not(mod(n,7))'", '-fps_mode', 'vfr', '-c:v', 'libx264'] if kind == 'vfr' else [
                    '-c:v', 'libx265', '-x265-params', 'log-level=error:pools=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc', '-pix_fmt', 'yuv420p10le',
                    '-color_trc', 'smpte2084', '-color_primaries', 'bt2020', '-colorspace', 'bt2020nc']
                self.runner.run([settings.FFMPEG_BINARY, '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x240:r=60:d=2',
                                 '-f', 'lavfi', '-i', 'sine=duration=2', *video, '-c:a', 'aac', '-shortest', str(source)])
                old, new = self.root / f'{kind}-old.mp4', self.root / f'{kind}-new.mp4'
                recipe = dict(source=source, width=160, height=120, lut_path=None)
                service._normalize_uncached(destination=old, early_fps=False, **recipe)
                service._normalize_uncached(destination=new, **recipe)
                self.assertEqual(self.decode(old), self.decode(new))
                self.assertEqual(self.decode(old, True), self.decode(new, True))

    def test_global_and_local_noise_keep_video_and_clean_buffers(self):
        service = AudioCleanupService(runner=self.runner)
        decisions = [NoiseReductionDecision(500, 2000, mode, ReductionStrength.LIGHT,
            'test', NoiseType.CONTINUOUS_NOISE, True, RecommendedAction.APPLY, False, .9, 'test', None)
            for mode in (ReductionMode.GLOBAL, ReductionMode.LOCAL)]
        output = self.root / 'global-local.mp4'
        result = service.apply(self.source, output, decisions)
        self.assertEqual(result.metrics['global'], 1)
        self.assertEqual(result.metrics['local'], 1)
        self.assertEqual(self.decode(self.source), self.decode(output))
        self.assertFalse(list(self.root.glob('noise_*.wav')))

    def test_noise_error_removes_owned_audio_but_preserves_input(self):
        service = AudioCleanupService(runner=self.runner)
        decision = NoiseReductionDecision(500, 2000, ReductionMode.GLOBAL, ReductionStrength.LIGHT,
            'test', NoiseType.CONTINUOUS_NOISE, True, RecommendedAction.APPLY, False, .9, 'test', None)
        with patch.object(service.provider, 'reduce', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                service.apply(self.source, self.root / 'failed.mp4', [decision])
        self.assertTrue(self.source.is_file())
        self.assertFalse(list(self.root.glob('noise_*.wav')))

    def test_concurrent_checkpoint_publications_remain_readable(self):
        from concurrent.futures import ThreadPoolExecutor
        cache = RenderCheckpointCache(self.runner)
        key = cache.key('concurrent', {'source': cache.identity(self.source)})
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(lambda _: cache.publish(key, self.source, expected_duration_ms=8000), range(2)))
        output = self.root / 'concurrent.mp4'
        self.assertIsNotNone(cache.restore(key, output, expected_duration_ms=8000))
        self.assertEqual(output.read_bytes(), self.source.read_bytes())


class RenderFallbackTests(SimpleTestCase):
    def test_only_seek_errors_retry_with_the_legacy_reader(self):
        from unittest.mock import Mock
        from website.external_media.exceptions import ExternalMediaError
        for message, calls in [('seek unsupported', 2), ('O processamento excedeu o tempo limite.', 1),
                               ('O armazenamento temporário ficou sem espaço.', 1)]:
            with self.subTest(message=message):
                service = VideoAssemblyService(runner=Mock())
                error = ExternalMediaError(message)
                with patch.object(service, '_concat_group_uncached', side_effect=[error, None]) as encode:
                    if calls == 2:
                        service._concat_group(['input'], 'output', [{'start_ms': 4000, 'end_ms': 5000}])
                        self.assertFalse(encode.call_args.kwargs['seek_inputs'])
                    else:
                        with self.assertRaises(ExternalMediaError):
                            service._concat_group(['input'], 'output', [{'start_ms': 4000, 'end_ms': 5000}])
                    self.assertEqual(encode.call_count, calls)
