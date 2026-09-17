import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase
from PIL import Image

from .external_media.ffmpeg_runner import FFmpegRunner
from .external_media.overlays import (
    OverlayAssetRenderer,
    OverlayRenderService,
    OverlayTimelineService,
    deep_merge,
)
from .models.external_media import ProjectOverlay


class OverlayTimelineServiceTests(SimpleTestCase):
    def test_deep_merge_preserves_preset_nested_values(self):
        self.assertEqual(
            deep_merge({'card': {'padding': 20, 'radius': 8}}, {'card': {'radius': 12}}),
            {'card': {'padding': 20, 'radius': 12}},
        )

    def test_block_end_timing_stays_inside_block(self):
        overlay = SimpleNamespace(
            timing_mode=ProjectOverlay.TimingMode.BLOCK_END,
            duration_ms=5000,
            start_ms=0,
            end_ms=None,
        )
        self.assertEqual(
            OverlayTimelineService.resolve_timing(overlay, 10_000, 13_000, 20_000),
            (10_000, 13_000),
        )

    def test_qr_rejects_invalid_web_url(self):
        with self.assertRaisesRegex(ValueError, 'URL válida'):
            OverlayTimelineService.validate_content(
                'QR_CODE', {}, {'payload': 'https:///sem-host'},
            )


class OverlayAssetRendererTests(SimpleTestCase):
    def test_qr_card_is_transparent_png_with_square_qr(self):
        overlay = {
            'type': 'QR_CODE_CARD',
            'content': {'payload': 'https://example.com', 'cta': 'Acesse agora'},
            'style': {'background': '#FFFFFF', 'qr_color': '#000000'},
            'position': {'width': .2},
        }
        with TemporaryDirectory() as temp:
            output = Path(temp) / 'card.png'
            size = OverlayAssetRenderer().render(overlay, 1920, 1080, output)
            image = Image.open(output)
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.size, size)
            self.assertGreater(image.height, image.width)

    def test_text_overlay_uses_supplied_content(self):
        overlay = {
            'type': 'TEXT', 'content': {'text': '20 de setembro'},
            'style': {'color': '#FFFFFF'}, 'position': {'width': .4},
        }
        with TemporaryDirectory() as temp:
            output = Path(temp) / 'text.png'
            OverlayAssetRenderer().render(overlay, 1920, 1080, output)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 100)


class OverlayRenderServiceTests(SimpleTestCase):
    @skipUnless(shutil.which(settings.FFMPEG_BINARY), 'FFmpeg não está disponível.')
    def test_ffmpeg_renders_text_and_animated_qr_from_same_timeline_data(self):
        runner = FFmpegRunner()
        with TemporaryDirectory() as temp:
            workdir = Path(temp)
            source, output = workdir / 'source.mp4', workdir / 'output.mp4'
            runner.run([
                settings.FFMPEG_BINARY, '-y', '-f', 'lavfi', '-i', 'color=c=black:s=640x360:d=2',
                '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-shortest',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source),
            ])
            overlays = [
                {
                    'id': 'event-date', 'type': 'TEXT', 'start_ms': 100, 'end_ms': 1700,
                    'content': {'text': '20 de setembro'}, 'position': {'x': .5, 'y': .8, 'width': .5},
                    'style': {'alignment': 'left', 'border_radius': 12},
                    'animation': {'type': 'POP', 'duration': .25, 'delay': .1},
                },
                {
                    'id': 'event-qr', 'type': 'QR_CODE', 'start_ms': 400, 'end_ms': 1800,
                    'content': {'payload': 'https://example.com'}, 'position': {'x': .85, 'y': .3, 'width': .18},
                    'style': {}, 'animation': {'type': 'SLIDE_UP', 'duration': .2},
                },
            ]
            result = OverlayRenderService(runner).apply(
                source, output, overlays, 640, 360, workdir,
            )
            self.assertEqual(result, output)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 1000)
