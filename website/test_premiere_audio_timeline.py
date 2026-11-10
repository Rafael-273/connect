import math
import json
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from website.external_media.exceptions import ExternalMediaError
from website.external_media.speech_edit import SpeechEditPlan, SpeechCut
from website.external_media.timeline import InternalTimelineBuilder, TimelineSource


class PremiereApprovedAudioTests(SimpleTestCase):
    def fixture(self):
        clips = [
            {'asset_id': asset, 'timeline_in_ms': start, 'timeline_out_ms': end,
             'source_in_ms': source_start, 'source_out_ms': source_end}
            for asset, start, end, source_start, source_end in [
                ('intro', 0, 200, 0, 200),
                ('take', 200, 500, 2200, 2500),
                ('take', 500, 800, 200, 500),
                ('take', 800, 1100, 1200, 1500),
            ]
        ]
        revision = SimpleNamespace(revision=212, timeline={'clips': clips}, edit_decision_set={'operations': []})
        sources = [
            (TimelineSource('intro', None, 'intro.mp4', 'intro.mp4', 'intro'), {'duration_ms': 200}),
            (TimelineSource('take', None, 'take.wav', 'take.wav', 'main', trim_start_ms=100), {'duration_ms': 3000}),
        ]
        project = SimpleNamespace(approved_timeline_revision=revision, configuration={}, template_version=SimpleNamespace(
            audio_noise_cleanup_enabled=False, dialogue_processing_enabled=False,
        ))
        return project, sources

    def test_export_uses_approved_order_and_source_ranges_without_reapplying_old_cuts(self):
        project, sources = self.fixture()
        plan = SpeechEditPlan((SpeechCut(200, 400, 'manual', ''),), 3200)
        clips, decisions, markers = InternalTimelineBuilder()._video_clips(project, sources, plan, {})
        self.assertEqual([(clip['asset_id'], clip['source_in_ms'], clip['source_out_ms']) for clip in clips],
                         [('intro', 0, 200), ('take', 2200, 2500), ('take', 200, 500), ('take', 1200, 1500)])
        self.assertEqual([marker['time_ms'] for marker in markers], [0, 200, 500, 800])
        self.assertEqual(clips[-1]['timeline_out_ms'], 1100)
        clips[0]['timeline_in_ms'] = 99
        self.assertEqual(project.approved_timeline_revision.timeline['clips'][0]['timeline_in_ms'], 0)

    def test_missing_source_is_rejected_instead_of_shifting_the_stem(self):
        project, sources = self.fixture()
        with self.assertRaisesMessage(ExternalMediaError, 'não está disponível'):
            InternalTimelineBuilder()._video_clips(project, sources[:1], SpeechEditPlan((), 3200), {})

    def test_inconsistent_snapshot_is_rejected_instead_of_silently_concatenating_audio(self):
        project, sources = self.fixture()
        project.approved_timeline_revision.timeline['clips'][1]['timeline_in_ms'] = 300
        with self.assertRaisesMessage(ExternalMediaError, 'intervalo inválido'):
            InternalTimelineBuilder()._video_clips(project, sources, SpeechEditPlan((), 3200), {})

    @skipUnless(shutil.which(settings.FFMPEG_BINARY), 'FFmpeg is required to verify exported audio samples')
    def test_exported_voice_wav_follows_reordered_video_including_silent_intro_and_source_trims(self):
        import numpy as np
        project, sources = self.fixture()
        with TemporaryDirectory() as directory:
            package = Path(directory) / 'package'
            (package / 'Audio').mkdir(parents=True)
            source = package / 'take.wav'
            rate = 48000
            with wave.open(str(source), 'wb') as audio:
                audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                audio.writeframes(b''.join(
                    struct.pack('<h', round(10000 * math.sin(2 * math.pi * tone * sample / rate)))
                    for tone in (220, 440, 660) for sample in range(rate)
                ))
            builder = InternalTimelineBuilder()
            builder._has_audio = lambda path: Path(path) == source
            clips, _, _ = builder._video_clips(project, sources, SpeechEditPlan((), 3200), {})
            assets, tracks = builder._dialogue_assets(project, sources, clips, package)
            output = package / 'Audio' / 'voz.wav'
            self.assertEqual(tracks[0]['timeline_out_ms'], 1100)
            probe = subprocess.run([
                settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'a:0',
                '-show_entries', 'stream=sample_rate,channels,bits_per_raw_sample,duration_ts', '-of', 'json', str(output),
            ], check=True, capture_output=True, text=True, timeout=15)
            audio = json.loads(probe.stdout)['streams'][0]
            self.assertEqual(int(audio['duration_ts']), round(1.1 * rate))
            self.assertEqual(int(audio['bits_per_raw_sample']), 24)
            self.assertEqual(audio['channels'], 2)
            for time, expected_tone in [(.05, None), (.3, 660), (.6, 220), (.9, 440)]:
                decoded = subprocess.run([
                    settings.FFMPEG_BINARY, '-v', 'error', '-ss', str(time), '-i', str(output),
                    '-t', '0.1', '-ac', '1', '-ar', str(rate), '-f', 's16le', '-',
                ], check=True, capture_output=True, timeout=15).stdout
                samples = np.frombuffer(decoded, dtype='<i2').astype(float)
                if expected_tone is None:
                    self.assertLess(np.max(abs(samples)), 10)
                else:
                    spectrum = abs(np.fft.rfft(samples))
                    frequency = np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(spectrum)]
                    self.assertAlmostEqual(frequency, expected_tone, delta=10)
