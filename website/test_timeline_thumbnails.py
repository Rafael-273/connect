import sys
import shutil
import struct
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import override_settings

from website.external_media.thumbnails import TimelineThumbnailService, ready_thumbnail, render_thumbnail, thumbnail_version


class TimelineThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.proxy = SimpleNamespace(pk=1, project_id=2, metadata={}, update_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            proxy_file=SimpleNamespace(name='proxy/take.mp4', storage=Mock()))
        self.manager = Mock()
        self.manager.select_for_update.return_value = self.manager
        self.manager.filter.return_value = self.manager
        self.manager.get.return_value = self.proxy
        self.manager.first.return_value = self.proxy
        self.manager.update.side_effect = lambda **values: setattr(self.proxy, 'metadata', values['metadata'])
        model = SimpleNamespace(objects=self.manager, Status=SimpleNamespace(READY='READY'))
        self.models = patch.dict(sys.modules, {'website.models.external_media': SimpleNamespace(ProjectSourceProxy=model)})
        self.models.start()
        self.addCleanup(self.models.stop)
        self.atomic = patch('website.external_media.thumbnails.transaction.atomic', side_effect=lambda: nullcontext())
        self.atomic.start()
        self.addCleanup(self.atomic.stop)

    @patch('website.external_media.thumbnails.transaction.on_commit', side_effect=lambda callback: callback())
    @patch.object(TimelineThumbnailService, 'enqueue')
    def test_repeated_opens_enqueue_once_and_do_not_change_video_version(self, enqueue, commit):
        before = thumbnail_version(self.proxy)
        TimelineThumbnailService.request(self.proxy)
        TimelineThumbnailService.request(self.proxy)
        enqueue.assert_called_once()
        self.assertEqual(thumbnail_version(self.proxy), before)
        self.assertNotIn('update_at', self.manager.update.call_args.kwargs)

    @patch.object(TimelineThumbnailService, 'enqueue')
    def test_ready_thumbnail_is_reused_and_replaced_video_invalidates_it(self, enqueue):
        self.proxy.metadata = {'timeline_thumbnail': {'status': 'READY', 'name': 'saved.jpg', 'version': thumbnail_version(self.proxy)}}
        self.assertEqual(ready_thumbnail(self.proxy), 'saved.jpg')
        TimelineThumbnailService.request(self.proxy)
        enqueue.assert_not_called()
        self.proxy.proxy_file.name = 'proxy/new-video.mp4'
        self.assertIsNone(ready_thumbnail(self.proxy))

    @patch('website.external_media.workspace.JobWorkspace')
    @patch('website.external_media.media_input.media_input_factory')
    @patch('website.external_media.thumbnails.render_thumbnail')
    def test_worker_saves_once_and_duplicate_delivery_reuses_the_saved_image(self, render, media, workspace):
        self.proxy.metadata = {'timeline_thumbnail': {'status': 'PENDING', 'version': thumbnail_version(self.proxy), 'token': 'job'}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'thumbnail.jpg'
            workspace.return_value.__enter__.return_value.file.return_value = output
            render.side_effect = lambda source, path, runner: path.write_bytes(b'jpeg')
            media.return_value.get_ffmpeg_input.return_value = '/tmp/proxy.mp4'
            self.proxy.proxy_file.storage.save.return_value = 'stored-thumbnail.jpg'
            TimelineThumbnailService.build(1, 'job')
            TimelineThumbnailService.build(1, 'job')
        render.assert_called_once()
        self.proxy.proxy_file.storage.save.assert_called_once()
        self.assertEqual(ready_thumbnail(self.proxy), 'stored-thumbnail.jpg')

    def test_render_is_a_single_small_frame_without_decoding_audio(self):
        runner = Mock()
        render_thumbnail('/tmp/source.mp4', '/tmp/thumbnail.jpg', runner)
        command = runner.run.call_args.args[0]
        self.assertEqual(command[command.index('-frames:v') + 1], '1')
        self.assertIn('-an', command)
        self.assertIn('scale=240:135', command[command.index('-vf') + 1])

    def test_real_ffmpeg_handles_the_odd_thumbnail_height_for_landscape_and_portrait(self):
        binary = shutil.which('ffmpeg')
        if not binary:
            try:
                import imageio_ffmpeg
                binary = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                self.skipTest('FFmpeg required for real JPEG validation')
        from website.external_media.ffmpeg_runner import FFmpegRunner
        with tempfile.TemporaryDirectory() as directory, override_settings(
                FFMPEG_BINARY=binary, EXTERNAL_MEDIA_WORKSPACE_ROOT=directory, EXTERNAL_MEDIA_FFMPEG_TIMEOUT=60):
            for width, height in ((320, 180), (180, 320)):
                with self.subTest(width=width, height=height):
                    source = Path(directory) / 'source.mp4'
                    output = Path(directory) / 'thumbnail.jpg'
                    runner = FFmpegRunner()
                    runner.run([binary, '-y', '-f', 'lavfi', '-i', f'color=c=blue:s={width}x{height}', '-frames:v', '1', '-threads', '1', str(source)])
                    render_thumbnail(source, output, runner)
                    data = output.read_bytes()
                    marker = data.index(b'\xff\xc0')
                    self.assertEqual(struct.unpack('>HH', data[marker + 5:marker + 9]), (135, 240))
