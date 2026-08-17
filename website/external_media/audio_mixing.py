from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from .audio_utils import (
    build_piecewise_expression, clamp, linear_gain_from_db, parse_mean_volume_db,
    settings_from_config,
)
from .ffmpeg_runner import FFmpegRunner

logger = logging.getLogger(__name__)

# Safety cap on how many speech blocks get their own spectral EQ window. Beyond this,
# the filter graph would grow large enough to slow down (or risk failing) the ffmpeg
# render for very long videos, so spectral ducking is skipped for the overflow blocks.
MAX_SPECTRAL_BLOCKS = 40


@dataclass(frozen=True)
class SpeechBlock:
    """A merged span of speech, grouped from words/cues that are close together.

    Small pauses between words/sentences are absorbed into the same block so the
    ducking envelope reacts to "someone is talking" rather than to every syllable.
    """

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self):
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class DuckingSettings:
    attack_ms: int = 250
    hold_ms: int = 180
    release_ms: int = 700
    base_duck_db: float = 8.0
    min_duck_db: float = 3.0
    max_duck_db: float = 14.0
    spectral_max_cut_db: float = 5.0
    spectral_center_hz: int = 1200
    spectral_bandwidth_octaves: float = 2.2

    @classmethod
    def from_config(cls, config):
        return settings_from_config(cls, config)


@dataclass
class AudioMixResult:
    path: Path
    metrics: dict = field(default_factory=dict)


def group_speech_blocks(intervals, gap_threshold_ms=450):
    """Merges (start_ms, end_ms) intervals separated by gaps smaller than the threshold.

    This is how word/subtitle-cue level timestamps become "Speech Blocks": the ducking
    envelope should hold steady through brief pauses instead of chasing every word.
    """
    ordered = sorted(
        (
            (int(start), int(end))
            for start, end in intervals
            if end is not None and start is not None and int(end) > int(start)
        ),
        key=lambda item: item[0],
    )
    blocks = []
    for start, end in ordered:
        if blocks and start - blocks[-1][1] <= gap_threshold_ms:
            blocks[-1] = (blocks[-1][0], max(blocks[-1][1], end))
        else:
            blocks.append((start, end))
    return [SpeechBlock(start, end) for start, end in blocks]


def clip_blocks_against_protected_ranges(blocks, protected_ranges):
    """Removes/trims speech-block time that falls inside a "manter bloco intacto" range.

    Protected ranges must never be touched by automatic ducking, so any overlap is cut
    out of the block instead of just skipping the whole block (which could otherwise
    leave a partially-protected block still triggering a duck outside the protected part).
    """
    if not protected_ranges:
        return list(blocks)
    result = []
    for block in blocks:
        segments = [(block.start_ms, block.end_ms)]
        for protected_start, protected_end in protected_ranges:
            next_segments = []
            for start, end in segments:
                if protected_end <= start or protected_start >= end:
                    next_segments.append((start, end))
                    continue
                if protected_start > start:
                    next_segments.append((start, protected_start))
                if protected_end < end:
                    next_segments.append((protected_end, end))
            segments = next_segments
        result.extend(SpeechBlock(start, end) for start, end in segments if end > start)
    return sorted(result, key=lambda item: item.start_ms)


def build_ducking_envelope(blocks, duration_ms, duck_gain, settings_):
    """Builds (time_seconds, gain) keyframes implementing attack/hold/release.

    Attack starts exactly at the speech block start (ramping down to `duck_gain`),
    hold keeps the reduced level through the block (this is what absorbs the small
    internal pauses already merged by `group_speech_blocks`), and release ramps back
    up to full level after the block ends. Consecutive blocks are protected against
    overlapping envelopes so a short gap between blocks never produces a gain increase
    followed immediately by another decrease (audible pumping).
    """
    duration_s = max(0.001, duration_ms / 1000)
    attack_s = max(0.01, settings_.attack_ms / 1000)
    release_s = max(0.01, settings_.release_ms / 1000)
    keyframes = [(0.0, 1.0)]
    ordered = sorted(blocks, key=lambda item: item.start_ms)
    for index, block in enumerate(ordered):
        start_s = block.start_ms / 1000
        end_s = block.end_ms / 1000
        attack_end_s = min(end_s, start_s + attack_s)
        next_start_s = ordered[index + 1].start_ms / 1000 if index + 1 < len(ordered) else duration_s
        release_end_s = min(release_s + end_s, next_start_s)
        keyframes.append((start_s, 1.0))
        keyframes.append((attack_end_s, duck_gain))
        keyframes.append((end_s, duck_gain))
        keyframes.append((max(end_s, release_end_s), 1.0))
    keyframes.append((duration_s, 1.0))
    deduped = []
    for point in keyframes:
        if deduped and abs(point[0] - deduped[-1][0]) < 0.0005:
            deduped[-1] = point
        else:
            deduped.append(point)
    return deduped


def build_spectral_windows(blocks, cut_db):
    """Returns (start_s, end_s) windows where a moderate EQ cut opens space for the voice."""
    if not blocks or cut_db <= 0:
        return []
    ordered = sorted(blocks, key=lambda item: item.start_ms)[:MAX_SPECTRAL_BLOCKS]
    return [(block.start_ms / 1000, block.end_ms / 1000) for block in ordered if block.duration_ms > 0]


class AudioMixingService:
    """Mixes dialogue and music into a single stereo bed using adaptive ducking.

    The output filter graph always keeps the legacy flat `amix` behaviour available as a
    safe fallback (no speech blocks, mixing disabled, or measurement failures fall back
    to it) following the "prefer preserving audio" principle: when in doubt, do less.
    """

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def measure_mean_volume_db(self, path: Path) -> float | None:
        try:
            result = self.runner.run_capture([
                settings.FFMPEG_BINARY, '-i', str(path), '-af', 'volumedetect',
                '-f', 'null', '-',
            ])
        except Exception:
            logger.warning('Não foi possível medir o volume médio de %s.', path, exc_info=True)
            return None
        return parse_mean_volume_db(result.stderr)

    def estimate_spectral_density(self, music_path: Path, settings_: DuckingSettings) -> float:
        """Rough 0..1 estimate of how much of the music's energy sits in the vocal band.

        Compares the mean volume of the full mix against a band-limited copy (roughly
        the frequency range where voice intelligibility matters most). If band-limiting
        barely reduces the level, most of the music's energy is already concentrated
        there, so spectral ducking should work harder; if band-limiting attenuates it a
        lot, the music is mostly outside the vocal range and spectral ducking has little
        to offer, so we scale its depth down accordingly.
        """
        full_db = self.measure_mean_volume_db(music_path)
        if full_db is None:
            return 0.0
        low = max(20, settings_.spectral_center_hz / (2 ** (settings_.spectral_bandwidth_octaves / 2)))
        high = settings_.spectral_center_hz * (2 ** (settings_.spectral_bandwidth_octaves / 2))
        try:
            result = self.runner.run_capture([
                settings.FFMPEG_BINARY, '-i', str(music_path), '-af',
                f'highpass=f={low:.0f},lowpass=f={high:.0f},volumedetect',
                '-f', 'null', '-',
            ])
        except Exception:
            logger.warning('Não foi possível estimar densidade espectral de %s.', music_path, exc_info=True)
            return 0.0
        band_db = parse_mean_volume_db(result.stderr)
        if band_db is None:
            return 0.0
        return clamp(1.0 - (full_db - band_db) / 12.0, 0.0, 1.0)

    @staticmethod
    def estimate_duck_db(music_mean_db, voice_mean_db, settings_: DuckingSettings) -> float:
        """Picks how many dB to duck the music based on the relative loudness of voice vs music.

        A soft/acoustic music bed measured well below the voice needs little ducking to
        stay out of the way; a dense/loud music bed close to the voice's level needs more.
        """
        if music_mean_db is None or voice_mean_db is None:
            return settings_.base_duck_db
        gap = voice_mean_db - music_mean_db
        return clamp(settings_.base_duck_db - 0.6 * gap, settings_.min_duck_db, settings_.max_duck_db)

    def mix(
        self, video_path: Path, music_path: Path, output_path: Path, *,
        music_volume: float, duration_ms: int, speech_blocks=None, protected_ranges=None,
        settings_: DuckingSettings = None, ducking_enabled: bool = True, spectral_enabled: bool = False,
    ) -> AudioMixResult:
        settings_ = settings_ or DuckingSettings()
        speech_blocks = clip_blocks_against_protected_ranges(speech_blocks or [], protected_ranges or [])
        metrics = {
            'speech_block_count': len(speech_blocks),
            'protected_range_count': len(protected_ranges or []),
        }
        if not ducking_enabled or not speech_blocks or duration_ms <= 0:
            self._mix_flat(video_path, music_path, output_path, music_volume)
            metrics['duck_db'] = 0.0
            metrics['mode'] = 'flat'
            return AudioMixResult(output_path, metrics)

        music_mean_db = self.measure_mean_volume_db(music_path)
        voice_mean_db = self.measure_mean_volume_db(video_path)
        duck_db = self.estimate_duck_db(music_mean_db, voice_mean_db, settings_)
        duck_gain = linear_gain_from_db(-duck_db)
        envelope = build_ducking_envelope(speech_blocks, duration_ms, duck_gain, settings_)
        volume_expression = build_piecewise_expression(envelope)

        spectral_density = 0.0
        spectral_windows = []
        if spectral_enabled:
            spectral_density = self.estimate_spectral_density(music_path, settings_)
            cut_db = spectral_density * settings_.spectral_max_cut_db
            spectral_windows = build_spectral_windows(speech_blocks, cut_db)

        filters = [
            f'[1:a]volume={float(music_volume):.3f}[music_base]',
            f"[music_base]volume=eval=frame:volume='{volume_expression}'[music_ducked]",
        ]
        music_label = '[music_ducked]'
        if spectral_windows:
            cut_db = spectral_density * settings_.spectral_max_cut_db
            eq_chain = []
            current_label = 'music_ducked'
            for index, (start_s, end_s) in enumerate(spectral_windows):
                next_label = f'music_eq{index}'
                eq_chain.append(
                    f'[{current_label}]equalizer=f={settings_.spectral_center_hz}:width_type=o:'
                    f"width={settings_.spectral_bandwidth_octaves:.2f}:g=-{cut_db:.2f}:"
                    f"enable='between(t\\,{start_s:.3f}\\,{end_s:.3f})'[{next_label}]"
                )
                current_label = next_label
            filters.extend(eq_chain)
            music_label = f'[{current_label}]'
        filters.append(f'[0:a]{music_label}amix=inputs=2:duration=first:dropout_transition=2[a]')

        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(video_path), '-stream_loop', '-1',
            '-i', str(music_path), '-filter_complex', ';'.join(filters),
            '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', str(output_path),
        ])
        metrics.update({
            'mode': 'adaptive',
            'music_mean_db': music_mean_db,
            'voice_mean_db': voice_mean_db,
            'duck_db': round(duck_db, 2),
            'spectral_applied': bool(spectral_windows),
            'spectral_density': round(spectral_density, 2) if spectral_enabled else None,
        })
        return AudioMixResult(output_path, metrics)

    def _mix_flat(self, video_path, music_path, output_path, music_volume):
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(video_path), '-stream_loop', '-1',
            '-i', str(music_path), '-filter_complex',
            f'[1:a]volume={float(music_volume):.3f}[music];'
            '[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]',
            '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', str(output_path),
        ])
