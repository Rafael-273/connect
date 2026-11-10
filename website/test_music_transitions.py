"""Shared geometry, FFmpeg filter graph and rendered equal-power regression tests."""
import json
from pathlib import Path
from unittest.mock import Mock

from django.test import SimpleTestCase

from website.external_media.audio_mixing import AudioMixingService
from website.external_media.music_transitions import transition_window


class MusicTransitionTests(SimpleTestCase):
    def test_browser_and_renderer_share_geometry_cases(self):
        cases = json.loads((Path(__file__).parent / 'external_media/fixtures/music_transitions.json').read_text())
        for case in cases:
            with self.subTest(case['name']):
                self.assertEqual(transition_window(case['left'], case['right'], case['source_duration_ms'], case.get('incoming_duration_ms')), case['expected'])

    def test_export_extends_handles_without_moving_cut_or_shortening_sequence(self):
        runner = Mock()
        service = AudioMixingService(runner)
        service._probe_duration_s = Mock(return_value=10)
        service.build_playlist(
            ['/tmp/a.wav', '/tmp/b.wav'], '/tmp/out.wav',
            transitions=['NONE'], clip_positions_ms=[(0, 4000), (4000, 8000)],
            clip_source_in_ms=[0, 1000], clip_ids=[1, 2],
            clip_transitions=[None, {'type': 'CONSTANT_POWER', 'from_track_id': 1, 'duration_ms': 2000}],
        )
        command = runner.run.call_args.args[0]
        graph = command[command.index('-filter_complex') + 1]
        self.assertIn('atrim=start=0.000:duration=5.000', graph)
        self.assertIn('afade=t=out:st=3.000:d=2.000:curve=qsin', graph)
        self.assertIn('afade=t=in:st=0.000:d=2.000:curve=qsin', graph)
        self.assertIn('adelay=3000:all=1[scheduled_music1]', graph)

    def test_missing_handles_do_not_loop_source_to_invent_a_transition(self):
        runner = Mock()
        service = AudioMixingService(runner)
        service._probe_duration_s = Mock(return_value=4)
        service.build_playlist(
            ['/tmp/a.wav', '/tmp/b.wav'], '/tmp/out.wav', transitions=['NONE'],
            clip_positions_ms=[(0, 4000), (4000, 8000)], clip_ids=[1, 2],
            clip_transitions=[None, {'type': 'CONSTANT_POWER', 'from_track_id': 1, 'duration_ms': 2000}],
        )
        command = runner.run.call_args.args[0]
        self.assertNotIn('afade=', command[command.index('-filter_complex') + 1])

    def test_rendered_crossfade_has_equal_power_and_exact_duration(self):
        import math
        import shutil
        import struct
        import tempfile
        import wave
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            self.skipTest('FFmpeg/ffprobe required for rendered audio parity')
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('a.wav', 'b.wav', 'mix.wav')]
            rate = 48000
            for path, frequency in zip(paths, (240, 480)):
                with wave.open(str(path), 'wb') as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(rate)
                    output.writeframes(b''.join(struct.pack('<h', round(4000 * math.sin(2 * math.pi * frequency * i / rate))) for i in range(rate * 8)))
            AudioMixingService().build_playlist(
                paths[:2], paths[2], transitions=['NONE'],
                clip_positions_ms=[(0, 3000), (3000, 6000)],
                clip_source_in_ms=[0, 1000], clip_ids=[1, 2],
                clip_transitions=[None, {'type': 'CONSTANT_POWER', 'from_track_id': 1, 'duration_ms': 2000}],
                sequence_duration_ms=8000,
            )
            with wave.open(str(paths[2]), 'rb') as result:
                self.assertEqual(result.getnframes(), rate * 8)
                samples = struct.unpack('<' + 'h' * result.getnframes() * 2, result.readframes(result.getnframes()))[::2]
            self.assertTrue(all(value == 0 for value in samples[rate * 6:]))
            # Project each tone onto a 20ms window: cosine/sine gains from the
            # exact same global clock as the interactive preview.
            for time in (2.1, 2.5, 3.0, 3.5, 3.9):
                start = round((time - .01) * rate)
                chunk = samples[start:start + 960]
                for frequency, expected in ((240, math.cos((time - 2) / 2 * math.pi / 2)), (480, math.sin((time - 2) / 2 * math.pi / 2))):
                    real = sum(value * math.cos(2 * math.pi * frequency * (start + i) / rate) for i, value in enumerate(chunk))
                    imag = sum(value * math.sin(2 * math.pi * frequency * (start + i) / rate) for i, value in enumerate(chunk))
                    # mono -> stereo uses a 1/sqrt(2) pan coefficient
                    amplitude = 2 * math.hypot(real, imag) / len(chunk) / (4000 / math.sqrt(2))
                    self.assertAlmostEqual(amplitude, expected, delta=.065)

    def test_empty_revision_playlist_does_not_restore_template_music(self):
        from website.external_media.services import MusicService
        self.assertEqual(MusicService.selected_tracks(object(), {'music_tracks': []}), [])
