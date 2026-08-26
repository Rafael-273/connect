from __future__ import annotations

import math
import re
import shutil
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from django.conf import settings

from website.ai import TranscriptionSegment

from .exceptions import ExternalMediaError


@dataclass(frozen=True)
class SpeechCut:
    start_ms: int
    end_ms: int
    kind: str
    label: str = ''

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms

    def as_dict(self):
        return {
            'start_ms': self.start_ms,
            'end_ms': self.end_ms,
            'kind': self.kind,
            'label': self.label,
        }


@dataclass(frozen=True)
class SpeechEditPlan:
    cuts: tuple[SpeechCut, ...]
    duration_ms: int
    crossfade_ms: int = 40

    @property
    def silence_count(self):
        return sum(cut.kind == 'silence' for cut in self.cuts)

    @property
    def filler_count(self):
        return sum(cut.kind == 'filler' for cut in self.cuts)

    @property
    def saved_ms(self):
        return sum(cut.duration_ms for cut in self.cuts)

    def as_preview(self):
        return {
            'silence_count': self.silence_count,
            'filler_count': self.filler_count,
            'saved_ms': self.saved_ms,
            'saved_seconds': round(self.saved_ms / 1000, 1),
            'cut_count': len(self.cuts),
        }

    def as_dict(self):
        return {
            'duration_ms': self.duration_ms,
            'crossfade_ms': self.crossfade_ms,
            'cuts': [cut.as_dict() for cut in self.cuts],
        }

    def without_ranges(self, ranges):
        protected = tuple(
            (
                max(0, int(item.get('start_ms') or 0)),
                max(0, int(item.get('end_ms') or 0)),
            )
            for item in (ranges or [])
            if int(item.get('end_ms') or 0) > int(item.get('start_ms') or 0)
        )
        if not protected:
            return self
        cuts = tuple(
            cut for cut in self.cuts
            if not any(cut.start_ms < end_ms and cut.end_ms > start_ms for start_ms, end_ms in protected)
        )
        if len(cuts) == len(self.cuts):
            return self
        return SpeechEditPlan(cuts, self.duration_ms, self.crossfade_ms)

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        cuts = tuple(
            SpeechCut(
                int(item.get('start_ms', 0)),
                int(item.get('end_ms', 0)),
                str(item.get('kind', 'silence')),
                str(item.get('label', '')),
            )
            for item in data.get('cuts', [])
        )
        return cls(
            cuts=cuts,
            duration_ms=int(data.get('duration_ms') or 1),
            crossfade_ms=int(data.get('crossfade_ms') or 40),
        )

    def remap_words(self, words):
        result = []
        for word in words:
            if any(
                cut.kind in ('filler', 'background_voice')
                and word.start_ms < cut.end_ms and word.end_ms > cut.start_ms
                for cut in self.cuts
            ):
                continue
            start_ms = self.remap_time(word.start_ms)
            end_ms = max(start_ms + 1, self.remap_time(word.end_ms))
            result.append(TranscriptionSegment(start_ms, end_ms, word.text, word.granularity))
        return result

    def remap_time(self, ms):
        """Shifts a timestamp from the original (pre-edit) timeline to the edited one."""
        shift = sum(cut.duration_ms for cut in self.cuts if cut.end_ms <= ms)
        return max(0, int(ms) - shift)

    def source_time(self, ms):
        """Maps a post-edit timestamp back onto the original source timeline."""
        edited_ms = max(0, int(ms))
        original_cursor = 0
        edited_cursor = 0
        for cut in self.cuts:
            kept_duration = max(0, cut.start_ms - original_cursor)
            if edited_ms <= edited_cursor + kept_duration:
                return original_cursor + edited_ms - edited_cursor
            edited_cursor += kept_duration
            original_cursor = cut.end_ms
        return original_cursor + edited_ms - edited_cursor


@dataclass(frozen=True)
class SpeechProfile:
    minimum_silence_ms: int
    short_keep_ratio: float
    medium_keep_ms: int
    long_keep_ms: int
    dramatic_bonus_ms: int
    filler_neighbor_gap_ms: int
    crossfade_ms: int


PROFILES = {
    'conservative': SpeechProfile(700, 0.75, 450, 550, 350, 260, 50),
    'balanced': SpeechProfile(250, 0.60, 250, 300, 180, 180, 40),
    'dynamic': SpeechProfile(250, 0.35, 180, 220, 80, 130, 30),
}

DEFAULT_FILLERS = (
    'eh', 'é', 'hum', 'hmm', 'ahn', 'ah', 'hã', 'tipo', 'né', 'então', 'assim',
)


class AudioActivity:
    """Small VAD/energy analyzer based on 30 ms PCM frames."""

    def __init__(self, wav_path: Path, frame_ms=30):
        with wave.open(str(wav_path), 'rb') as stream:
            self.rate = stream.getframerate()
            channels = stream.getnchannels()
            width = stream.getsampwidth()
            raw = stream.readframes(stream.getnframes())
        if width != 2:
            raise ExternalMediaError('A análise de voz requer áudio PCM de 16 bits.')
        samples = np.frombuffer(raw, dtype='<i2').astype(np.float32)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        frame_size = max(1, round(self.rate * frame_ms / 1000))
        usable = math.ceil(len(samples) / frame_size) * frame_size
        samples = np.pad(samples, (0, usable - len(samples)))
        frames = samples.reshape(-1, frame_size)
        rms = np.sqrt(np.mean(np.square(frames), axis=1) + 1.0)
        self.db = 20 * np.log10(rms / 32768.0)
        self.frame_ms = frame_ms
        noise_floor = float(np.percentile(self.db, 20)) if self.db.size else -60.0
        # Adaptive threshold: robust to a quiet room and to constant background noise.
        self.voice_threshold_db = min(-30.0, max(-48.0, noise_floor + 10.0))
        self.breath_threshold_db = self.voice_threshold_db - 7.0

    def _slice(self, start_ms, end_ms):
        start = max(0, int(start_ms // self.frame_ms))
        end = min(len(self.db), max(start + 1, int(math.ceil(end_ms / self.frame_ms))))
        return self.db[start:end]

    def silence_ratio(self, start_ms, end_ms):
        values = self._slice(start_ms, end_ms)
        return float(np.mean(values < self.voice_threshold_db)) if values.size else 0.0

    def contains_breath(self, start_ms, end_ms):
        values = self._slice(start_ms, end_ms)
        if not values.size:
            return False
        breath_frames = (values >= self.breath_threshold_db) & (values < self.voice_threshold_db)
        return float(np.mean(breath_frames)) >= 0.12

    def average_db(self, start_ms, end_ms):
        """Robust loudness estimate for a known speech interval."""
        values = self._slice(start_ms, end_ms)
        return float(np.median(values)) if values.size else -96.0


class SpeechEditAnalyzer:
    def analyze(
        self, words, wav_path: Path, duration_ms: int, *, remove_silence=True,
        remove_fillers=True, configuration=None, block_ranges=None,
    ) -> SpeechEditPlan:
        configuration = configuration or {}
        profile = PROFILES.get(configuration.get('profile', 'balanced'), PROFILES['balanced'])
        # An explicit empty list means that this template opted out of all
        # vocabulary items. Only older configurations without this key use the
        # legacy defaults.
        filler_words = (
            configuration['filler_words']
            if 'filler_words' in configuration
            else DEFAULT_FILLERS
        )
        fillers = {self._normalize(value) for value in filler_words if self._normalize(value)}
        activity = AudioActivity(wav_path)
        ordered = sorted(words, key=lambda item: item.start_ms)
        cuts = []
        if remove_silence:
            cuts.extend(self._silence_cuts(ordered, activity, profile))
            cuts.extend(self._leading_take_cuts(ordered, block_ranges, profile))
        if remove_fillers and ordered and all(word.granularity == 'word' for word in ordered):
            cuts.extend(self._filler_cuts(ordered, activity, profile, fillers))
        cuts = self._merge_safe_cuts(cuts, duration_ms)
        return SpeechEditPlan(tuple(cuts), duration_ms, profile.crossfade_ms)

    @staticmethod
    def _leading_take_cuts(words, block_ranges, profile):
        """Removes a detached inhale/silence before speech starts in each take.

        A regular silence cut only has a previous and a following word. At the start
        of a clip there is no previous word, which previously left a short image of a
        presenter inhaling before the first spoken phrase. Keep a tiny lead-in so the
        first consonant is never clipped, but do not preserve a standalone breath.
        """
        if not words or not block_ranges:
            return []
        minimum_lead_ms = max(450, profile.minimum_silence_ms)
        # Word timestamps may start a little late; leave enough of the natural
        # lead-in so the first syllable does not feel abruptly cut.
        speech_guard_ms = 150
        cuts = []
        for item in block_ranges:
            start_ms = max(0, int(item.get('start_ms') or 0))
            end_ms = max(start_ms, int(item.get('end_ms') or 0))
            first_word = next(
                (
                    word for word in words
                    if word.start_ms >= start_ms and word.start_ms < end_ms
                ),
                None,
            )
            if not first_word or first_word.start_ms - start_ms < minimum_lead_ms:
                continue
            cut_end = max(start_ms, first_word.start_ms - speech_guard_ms)
            if cut_end - start_ms >= 120:
                cuts.append(SpeechCut(start_ms, cut_end, 'silence'))
        return cuts

    def _silence_cuts(self, words, activity, profile):
        cuts = []
        for previous, following in zip(words, words[1:]):
            gap = following.start_ms - previous.end_ms
            if gap < profile.minimum_silence_ms or activity.silence_ratio(previous.end_ms, following.start_ms) < 0.68:
                continue
            if gap < 700:
                keep = round(gap * profile.short_keep_ratio)
            elif gap <= 1500:
                keep = profile.medium_keep_ms
            else:
                keep = profile.long_keep_ms
            if self._ends_sentence(previous.text):
                keep += profile.dramatic_bonus_ms
            if activity.contains_breath(previous.end_ms, following.start_ms):
                keep = max(keep, round(gap * 0.55))
            removable = gap - min(gap, keep)
            if removable < 120:
                continue
            left_keep = (gap - removable) // 2
            cuts.append(SpeechCut(
                previous.end_ms + left_keep,
                following.start_ms - (gap - removable - left_keep),
                'silence',
            ))
        return cuts

    def _filler_cuts(self, words, activity, profile, fillers):
        cuts = []
        # The old limit required a full profile-sized pause on *both* sides of
        # the filler (180 ms in the default profile). Whisper word boundaries
        # are commonly tighter than that, even for an isolated "hum...". Keep
        # the word safety window, but accept a smaller verified-quiet gap.
        word_safety_ms = 100
        verified_gap_ms = 110
        for index, word in enumerate(words):
            if self._normalize(word.text) not in fillers or word.end_ms - word.start_ms > 1600:
                continue
            previous = words[index - 1] if index else None
            following = words[index + 1] if index + 1 < len(words) else None
            before = word.start_ms - previous.end_ms if previous else profile.filler_neighbor_gap_ms
            after = following.start_ms - word.end_ms if following else profile.filler_neighbor_gap_ms
            # Never cut a filler glued to another word. This remains the key
            # safeguard against truncating syllables when timestamps drift.
            if before < verified_gap_ms or after < verified_gap_ms:
                continue
            # A spoken filler must be surrounded by verified low activity. A
            # 110 ms gap lets natural isolated fillers be removed while the
            # 100 ms protected region around neighboring words stays intact.
            before_start = max(0, word.start_ms - min(90, before))
            after_end = word.end_ms + min(90, after)
            if activity.silence_ratio(before_start, word.start_ms) < 0.45:
                continue
            if activity.silence_ratio(word.end_ms, after_end) < 0.45:
                continue
            margin = min(80, (before - word_safety_ms) // 2, (after - word_safety_ms) // 2)
            start = word.start_ms - margin
            end = word.end_ms + margin
            if previous and start < previous.end_ms + word_safety_ms:
                continue
            if following and end > following.start_ms - word_safety_ms:
                continue
            cuts.append(SpeechCut(start, end, 'filler', word.text.strip()))
        return cuts

    @staticmethod
    def _merge_safe_cuts(cuts, duration_ms):
        result = []
        for cut in sorted(cuts, key=lambda item: (item.start_ms, item.end_ms)):
            cut = SpeechCut(max(0, cut.start_ms), min(duration_ms, cut.end_ms), cut.kind, cut.label)
            if cut.duration_ms < 80:
                continue
            if result and cut.start_ms <= result[-1].end_ms:
                previous = result[-1]
                kind = 'filler' if 'filler' in {previous.kind, cut.kind} else 'silence'
                result[-1] = SpeechCut(previous.start_ms, max(previous.end_ms, cut.end_ms), kind, previous.label or cut.label)
            else:
                result.append(cut)
        return result

    @staticmethod
    def _normalize(value):
        value = unicodedata.normalize('NFKD', str(value).lower())
        value = ''.join(char for char in value if not unicodedata.combining(char))
        return re.sub(r'[^a-z0-9]+', '', value)

    @staticmethod
    def _ends_sentence(value):
        return bool(re.search(r'[.!?;:]\s*$', value or ''))


class SpeechEditService:
    def __init__(self, runner):
        self.runner = runner

    def extract_analysis_audio(self, video_path: Path, wav_path: Path):
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(video_path), '-vn', '-ac', '1',
            '-ar', '16000', '-c:a', 'pcm_s16le', str(wav_path),
        ])

    def duration_ms(self, video_path: Path):
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path),
        ])
        return max(1, round(float(output.strip()) * 1000))

    def apply(self, source_path: Path, output_path: Path, plan: SpeechEditPlan):
        if not plan.cuts:
            shutil.copyfile(source_path, output_path)
            return
        keeps = self._keep_intervals(plan)
        if not keeps:
            raise ExternalMediaError('Os cortes de fala removeriam todo o vídeo; edição cancelada.')
        filters = []
        concat_inputs = []
        for index, interval in enumerate(keeps):
            start_ms, end_ms = interval
            start, end = start_ms / 1000, end_ms / 1000
            filters.extend([
                f'[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,settb=AVTB[v{index}]',
                f'[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]',
            ])
            concat_inputs.append(f'[v{index}][a{index}]')
        filters.append(
            ''.join(concat_inputs) + f'concat=n={len(keeps)}:v=1:a=1[v][a]'
        )
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(source_path), '-filter_complex', ';'.join(filters),
            '-map', '[v]', '-map', '[a]', '-c:v', 'libx264',
            '-preset', settings.EXTERNAL_MEDIA_INTERMEDIATE_PRESET,
            '-crf', str(settings.EXTERNAL_MEDIA_INTERMEDIATE_CRF), '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output_path),
        ])

    @staticmethod
    def _keep_intervals(plan):
        intervals = []
        cursor = 0
        for cut in plan.cuts:
            if cut.start_ms > cursor:
                intervals.append((cursor, cut.start_ms))
            cursor = max(cursor, cut.end_ms)
        if cursor < plan.duration_ms:
            intervals.append((cursor, plan.duration_ms))
        return [item for item in intervals if item[1] - item[0] >= 80]
