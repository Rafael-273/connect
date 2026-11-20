import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.test import SimpleTestCase
from PIL import Image

from website.external_media.broll import BrollRenderService
from website.external_media.overlays import OverlayRenderService
from website.external_media.transitions import TransitionFilters, effective_edges, normalize_transitions


class VisualTransitionTests(SimpleTestCase):
    def test_wipe_boundary_matches_preview_at_same_playhead(self):
        ffmpeg = shutil.which(settings.FFMPEG_BINARY)
        node = shutil.which('node')
        if not ffmpeg or not node:
            self.skipTest('FFmpeg and Node are required for renderer comparison')
        script = ('const [modulePath, edges] = process.argv.slice(1);'
                  'process.stdout.write(JSON.stringify(require(modulePath).sample(JSON.parse(edges), 200, 1000)));')
        module_path = Path(settings.BASE_DIR) / 'static' / 'external_media_transitions.js'
        for softness in (0, 20):
            with self.subTest(softness=softness):
                transitions = normalize_transitions({'in': {
                    'duration_ms': 800, 'easing': 'ease_out',
                    'effects': [{'type': 'wipe', 'direction': 'left', 'softness': softness}],
                }})
                state = json.loads(subprocess.check_output([node, '-e', script, str(module_path), json.dumps(transitions)]))
                if softness:
                    transparent_stop = float(state['mask'].split()[-1].rstrip('%)'))
                    expected_visible = .8 * (transparent_stop - softness / 2)
                else:
                    expected_visible = 80 * (1 - float(state['clip'].split()[1].rstrip('%')) / 100)
                transition = TransitionFilters({'transitions': transitions}, 0, 1000, 0)
                graph = transition.prepare('[1:v]', '[wipe]', [])
                graph += ';[0:v][wipe]overlay=x=40:y=20:format=auto[v]'
                result = subprocess.check_output([
                    ffmpeg, '-v', 'error', '-filter_complex_threads', '1',
                    '-f', 'lavfi', '-i', 'color=black:s=160x80:r=25:d=1',
                    '-f', 'lavfi', '-i', 'color=white:s=80x40:r=25:d=1',
                    '-filter_complex', graph, '-map', '[v]', '-ss', '0.2', '-frames:v', '1',
                    '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1',
                ], timeout=30)
                image = Image.frombytes('RGB', (160, 80), result)
                visible = sum(image.getpixel((x, 40))[0] > 128 for x in range(40, 120))
                self.assertAlmostEqual(visible, expected_visible, delta=2)

    def test_validates_effects_and_scales_durations_after_trim(self):
        transitions = normalize_transitions({
            'in': {'duration_ms': 900, 'effects': [{'type': 'blur_fade', 'blur': 99}, {'type': 'slide'}]},
            'out': {'duration_ms': 600, 'effects': [{'type': 'zoom'}, {'type': 'wipe'}]},
        })
        self.assertEqual(transitions['in']['effects'][0]['blur'], 32)
        self.assertEqual([duration for _, _, duration in effective_edges({'transitions': transitions}, 500)],
                         [.3, .2])
        for invalid in [{'type': 'slide', 'direction': 'diagonal'}, {'type': 'zoom', 'scale': float('nan')}]:
            with self.assertRaises(ValueError):
                normalize_transitions({'out': {'effects': [invalid]}})
        with self.assertRaises(ValueError):
            normalize_transitions({'in': {'effects': [{'type': 'wipe'}] * 2}})

    def test_broll_and_text_render_with_combined_transitions(self):
        ffmpeg = shutil.which(settings.FFMPEG_BINARY)
        if not ffmpeg:
            self.skipTest('FFmpeg unavailable')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, image, video = path / 'source.mp4', path / 'broll.png', path / 'broll-source.mp4'
            Image.new('RGB', (100, 100), 'red').save(image)
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=black:s=320x180:r=30:d=3',
                            '-c:v', 'libx264', str(source)], check=True, capture_output=True, timeout=30)
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=30:d=3',
                            '-c:v', 'libx264', str(video)], check=True, capture_output=True, timeout=30)
            transitions = normalize_transitions({
                'in': {'duration_ms': 1000, 'easing': 'linear', 'effects': [
                    {'type': 'slide', 'direction': 'left'}, {'type': 'wipe', 'direction': 'left'},
                    {'type': 'blur_fade', 'blur': 12}, {'type': 'zoom', 'scale': .65},
                ]},
                'out': {'duration_ms': 500, 'easing': 'linear', 'effects': [
                    {'type': 'zoom', 'scale': 1.4}, {'type': 'blur_fade', 'blur': 12},
                ]},
            })
            common = {'start_ms': 0, 'end_ms': 2500, 'transitions': transitions}
            transform = {'x': .7, 'y': .5, 'scale': .3, 'rotation': 20, 'opacity': .75}
            broll = {**common, 'asset_id': 'img', 'display_mode': 'OVERLAY', 'transform': transform}
            visual_asset = SimpleNamespace(public_id='img', media_type='IMAGE', file=SimpleNamespace(path=str(image)))
            video_asset = SimpleNamespace(public_id='video', media_type='VIDEO', file=SimpleNamespace(path=str(video)))
            video_item = {**common, 'asset_id': 'video', 'display_mode': 'FULLSCREEN', 'layer': 1,
                          'transform': {'scale': 1.2}, 'transitions': {'in': transitions['in']}}
            project = SimpleNamespace(public_id='transition-test',
                                      broll_assets=SimpleNamespace(filter=lambda **_: [visual_asset, video_asset]))
            # Exercise both renderer entry points used by the workflow.
            for renderer, output in [
                (lambda output: BrollRenderService().apply(project, source, output, [broll, video_item], 320, 180, duration_ms=3000),
                 path / 'broll.mp4'),
                (lambda output: OverlayRenderService().apply(source, output, [{
                    **common, 'type': 'TEXT', 'content': {'text': 'Texto'},
                    'position': {'x': .7, 'y': .5, 'width': .4, 'rotation': 20, 'opacity': .75},
                    'style': {'font_size': 24, 'alignment': 'left', 'background': '#ff0000'},
                }], 320, 180, path), path / 'text.mp4'),
            ]:
                renderer(output)
                def frame_at(seconds):
                    data = subprocess.run([ffmpeg, '-v', 'error', '-ss', str(seconds), '-i', str(output),
                                           '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1'],
                                          check=True, capture_output=True, timeout=30).stdout
                    return Image.frombytes('RGB', (320, 180), data)
                self.assertLess(max(frame_at(0).getpixel((224, 90))), 10)
                self.assertGreater(max(frame_at(1.5).getpixel((224, 90))), 40)
                self.assertLess(max(frame_at(2.7).getpixel((224, 90))), 10)
                if output.name == 'broll.mp4':
                    middle = frame_at(1.5)
                    red, _, blue = middle.getpixel((224, 90))
                    self.assertGreater(red, blue)
                    self.assertGreater(middle.getpixel((40, 90))[2], 100)

    def test_chunk_uses_original_clip_clock(self):
        item = {'transitions': normalize_transitions({'in': {'effects': [{'type': 'slide'}], 'duration_ms': 1200}})}
        active = TransitionFilters(item, 1000, 4000, 1500, segment_end_ms=2000)
        self.assertIn('-0.500000000', active.position('0', '0')[0])
        after_entry = TransitionFilters(item, 1000, 4000, 2500, segment_end_ms=3500)
        self.assertEqual(after_entry.position('x', 'y'), ('x', 'y'))
