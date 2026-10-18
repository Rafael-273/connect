from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from .audio_utils import (
    build_piecewise_expression, clamp, linear_gain_from_db, parse_mean_volume_db,
    settings_from_config,
)
from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner

logger = logging.getLogger(__name__)

# Safety cap on how many speech blocks get their own spectral EQ window. Beyond this,
# the filter graph would grow large enough to slow down (or risk failing) the ffmpeg
# render for very long videos, so spectral ducking is skipped for the overflow blocks.
MAX_SPECTRAL_BLOCKS = 40
# Measuring each speaking segment is more accurate than measuring the entire
# timeline (which includes silence), but must stay bounded on long interviews.
MAX_DYNAMIC_DUCK_BLOCKS = 32

# These are deliberately configuration presets, not a second mixing engine.  The
# renderer still uses DuckingSettings and the same FFmpeg envelope for every
# profile; a profile only supplies a sensible starting point for a template.
DUCKING_PROFILES = {
    'emotional': {
        'label': 'Suave / Emocional',
        'base_duck_db': 6.0, 'min_duck_db': 4.0, 'max_duck_db': 9.0,
        'target_voice_to_music_gap_db': 10.0,
        'attack_ms': 260, 'hold_ms': 450, 'release_ms': 1400,
        'speech_gap_hold_ms': 2400, 'between_speech_gain': 1.0,
        'spectral_max_cut_db': 3.0,
    },
    'balanced': {
        'label': 'Equilibrado',
        'base_duck_db': 11.0, 'min_duck_db': 8.0, 'max_duck_db': 15.0,
        'target_voice_to_music_gap_db': 14.0,
        'attack_ms': 140, 'hold_ms': 300, 'release_ms': 850,
        'speech_gap_hold_ms': 1800, 'between_speech_gain': 1.0,
        'spectral_max_cut_db': 5.0,
    },
    'clarity': {
        'label': 'Intenso / Clareza',
        'base_duck_db': 15.0, 'min_duck_db': 11.0, 'max_duck_db': 20.0,
        'target_voice_to_music_gap_db': 17.0,
        'attack_ms': 80, 'hold_ms': 220, 'release_ms': 550,
        'speech_gap_hold_ms': 1200, 'between_speech_gain': 1.0,
        'spectral_max_cut_db': 7.0,
    },
}
DEFAULT_DUCKING_PROFILE = 'balanced'

# Keep all persisted template and revision configurations forwards-compatible.
# In particular, projects created before the editor gained voice gain and
# sensitivity controls do not contain these keys.
DUCKING_DEFAULTS = {
    'attack_ms': 140,
    'hold_ms': 300,
    'release_ms': 850,
    'speech_gap_hold_ms': 1800,
    'base_duck_db': 11.0,
    'min_duck_db': 8.0,
    'max_duck_db': 15.0,
    'target_voice_to_music_gap_db': 14.0,
    'spectral_max_cut_db': 5.0,
    'spectral_center_hz': 1200,
    'spectral_bandwidth_octaves': 2.2,
    'between_speech_gain': 1.0,
    'voice_activation_db': -96.0,
    'voice_gain_db': 0.0,
}


def resolved_ducking_config(config=None):
    """Return backwards-compatible flat settings from a template/revision config.

    Older templates stored only flat JSON. Newer ones may use a profile and a
    ``ducking`` object. Keeping this normalization at the audio boundary means
    old projects render identically and all consumers share one interpretation.
    """
    config = dict(config) if isinstance(config, dict) else {}
    nested = config.pop('ducking', {})
    nested = dict(nested) if isinstance(nested, dict) else {}
    profile = str(config.get('profile') or nested.get('profile') or DEFAULT_DUCKING_PROFILE)
    if profile not in DUCKING_PROFILES:
        profile = DEFAULT_DUCKING_PROFILE
    # A profile may itself be from an older deployment and therefore omit
    # controls introduced later. Start with stable defaults before applying it.
    resolved = {**DUCKING_DEFAULTS, **DUCKING_PROFILES[profile]}
    # Flat values have historically been accepted, and should win over the
    # profile. Values inside ducking are the structured equivalent.
    resolved.update(nested)
    resolved.update({key: value for key, value in config.items() if key != 'profile'})
    resolved['profile'] = profile
    return resolved


def ducking_options(config=None):
    """Normalize legacy boolean ducking values into the structured format.

    Early project revisions persisted ``ducking: true``/``false``. Newer
    revisions persist an object so they can also carry spectral settings.
    Render and preview consumers must accept both while old projects exist.
    """
    config = config if isinstance(config, dict) else {}
    value = config.get('ducking')
    if isinstance(value, dict):
        return value
    if isinstance(value, bool):
        return {'enabled': value}
    return {}


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
    attack_ms: int = 140
    hold_ms: int = 300
    release_ms: int = 850
    # Subtitle cues often have a small gap around breaths or natural pauses.
    # Keep them in the same dialogue bed so the music does not audibly pump.
    speech_gap_hold_ms: int = 1800
    # Keep the voice distinctly in front without making the music disappear. The
    # former 14–20 dB range was too strong; 5–12 dB, on the other hand, still let
    # a dense track compete with speech. This middle range is the default balance.
    base_duck_db: float = 11.0
    min_duck_db: float = 8.0
    max_duck_db: float = 15.0
    # Desired separation between the measured dialogue and the music bed while
    # somebody is speaking. This is applied after `music_volume`.
    target_voice_to_music_gap_db: float = 14.0
    spectral_max_cut_db: float = 5.0
    spectral_center_hz: int = 1200
    spectral_bandwidth_octaves: float = 2.2
    # Gain outside speech.  It is relative to the template/project music volume,
    # so 1.0 preserves legacy behaviour and 0.8 keeps the bed subtly restrained.
    between_speech_gain: float = 1.0
    # Quiet cue fragments (breaths, room tone) should not pull music down. This
    # is opt-in by default to preserve existing subtitle-derived timing.
    voice_activation_db: float = -96.0
    # Applied before dialogue and music are combined. Useful for a small manual
    # voice lift without introducing another compressor/mastering pass.
    voice_gain_db: float = 0.0

    @classmethod
    def from_config(cls, config):
        return settings_from_config(cls, resolved_ducking_config(config))


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
    """Removes/trims speech-block time that falls inside a protected edit range.

    This is used by audio transformations that must not change an intact source block,
    such as dialogue leveling. Music ducking intentionally does not use this helper:
    the music still needs to sit behind speech in those blocks.
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
    rest_gain = clamp(float(settings_.between_speech_gain), 0.0, 1.5)
    keyframes = [(0.0, rest_gain)]
    ordered = sorted(blocks, key=lambda item: item.start_ms)
    for index, block in enumerate(ordered):
        start_s = max(0.0, block.start_ms / 1000)
        end_s = min(duration_s, block.end_ms / 1000)
        if end_s <= start_s:
            continue
        attack_end_s = min(end_s, start_s + attack_s)
        next_start_s = ordered[index + 1].start_ms / 1000 if index + 1 < len(ordered) else duration_s
        # Do not return to full volume if the next speech block starts before the
        # release can finish. A full-volume keyframe at that boundary caused brief,
        # audible music spikes between consecutive spoken phrases.
        continues_into_next_block = next_start_s - end_s <= release_s
        if index == 0 or ordered[index - 1].end_ms / 1000 + release_s < start_s:
            keyframes.append((start_s, rest_gain))
            keyframes.append((attack_end_s, duck_gain))
        else:
            keyframes.append((start_s, duck_gain))
        keyframes.append((end_s, duck_gain))
        if not continues_into_next_block and end_s < duration_s:
            keyframes.append((min(end_s + release_s, duration_s), rest_gain))
    keyframes.append((duration_s, rest_gain))
    deduped = []
    for point in keyframes:
        if deduped and abs(point[0] - deduped[-1][0]) < 0.0005:
            deduped[-1] = point
        else:
            deduped.append(point)
    return deduped


def build_dynamic_ducking_envelope(blocks, duration_ms, duck_gains, settings_):
    """Build a smooth ducking envelope with a gain chosen for each speech block."""
    if not blocks:
        rest_gain = clamp(float(settings_.between_speech_gain), 0.0, 1.5)
        return [(0.0, rest_gain), (max(0.001, duration_ms / 1000), rest_gain)]
    duration_s = max(0.001, duration_ms / 1000)
    attack_s = max(0.01, settings_.attack_ms / 1000)
    release_s = max(0.01, settings_.release_ms / 1000)
    rest_gain = clamp(float(settings_.between_speech_gain), 0.0, 1.5)
    keyframes = [(0.0, rest_gain)]
    ordered = list(zip(sorted(blocks, key=lambda item: item.start_ms), duck_gains))
    previous_end = None
    previous_gain = rest_gain
    for index, (block, duck_gain) in enumerate(ordered):
        start_s = max(0.0, block.start_ms / 1000)
        end_s = min(duration_s, block.end_ms / 1000)
        if end_s <= start_s:
            continue
        next_start_s = ordered[index + 1][0].start_ms / 1000 if index + 1 < len(ordered) else duration_s
        previous_release_end = (previous_end + release_s) if previous_end is not None else -1
        if previous_end is None or start_s > previous_release_end:
            keyframes.append((start_s, rest_gain))
        else:
            # Keep the bed under speech across short pauses, transitioning to the
            # next block's measured target instead of briefly rising to full level.
            keyframes.append((start_s, previous_gain))
        keyframes.append((min(end_s, start_s + attack_s), duck_gain))
        keyframes.append((end_s, duck_gain))
        if next_start_s - end_s > release_s and end_s < duration_s:
            keyframes.append((min(duration_s, end_s + release_s), rest_gain))
        previous_end = end_s
        previous_gain = duck_gain
    keyframes.append((duration_s, rest_gain))
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


def normalize_volume_keyframes(keyframes, duration_ms):
    """Normalize manual music automation as gain-dB points relative to base volume."""
    duration_ms = max(1, int(duration_ms or 1))
    points = []
    for item in keyframes or []:
        if not isinstance(item, dict):
            continue
        try:
            time_ms = max(0, min(duration_ms, int(item.get('time_ms'))))
            gain_db = clamp(float(item.get('gain_db')), -60.0, 12.0)
        except (TypeError, ValueError):
            continue
        points.append((time_ms / 1000, linear_gain_from_db(gain_db)))
    points.sort(key=lambda item: item[0])
    deduped = []
    for point in points:
        if deduped and point[0] == deduped[-1][0]:
            deduped[-1] = point
        else:
            deduped.append(point)
    duration_s = duration_ms / 1000
    if not deduped:
        return [(0.0, 1.0), (duration_s, 1.0)]
    if deduped[0][0] > 0:
        deduped.insert(0, (0.0, deduped[0][1]))
    if deduped[-1][0] < duration_s:
        deduped.append((duration_s, deduped[-1][1]))
    return deduped


def _gain_at(keyframes, time_s):
    for (start, value), (end, next_value) in zip(keyframes, keyframes[1:]):
        if time_s <= end:
            ratio = (time_s - start) / max(.001, end - start)
            return value + (next_value - value) * ratio
    return keyframes[-1][1]


def combine_gain_envelopes(*envelopes):
    """Multiply smooth automation envelopes without discarding manual choices."""
    valid = [item for item in envelopes if item]
    if not valid:
        return []
    times = sorted({time for envelope in valid for time, _ in envelope})
    return [(time, math.prod(_gain_at(envelope, time) for envelope in valid)) for time in times]


class AudioMixingService:
    """Mixes dialogue and music into a single stereo bed using adaptive ducking.

    The output filter graph always keeps the legacy flat `amix` behaviour available as a
    safe fallback (no speech blocks, mixing disabled, or measurement failures fall back
    to it) following the "prefer preserving audio" principle: when in doubt, do less.
    """

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def build_playlist(
        self, music_paths, output_path, transitions=None, durations_ms=None,
        clip_durations_ms=None, clip_positions_ms=None, clip_source_in_ms=None,
    ):
        """Create one looping-ready music bed from an ordered playlist.

        ``qsin`` curves form an equal-power (constant-power) crossfade, so the
        perceived loudness stays steady while one song hands over to the next.
        The resulting WAV is then consumed by the existing ducking/mixing path.
        """
        paths = [Path(path) for path in (music_paths or [])]
        if not paths:
            return None
        transitions = list(transitions or [])
        durations_ms = list(durations_ms or [])
        clip_durations_ms = list(clip_durations_ms or [])
        clip_positions_ms = list(clip_positions_ms or [])
        clip_source_in_ms = list(clip_source_in_ms or [])
        scheduled_positions = []
        for index in range(len(paths)):
            position = clip_positions_ms[index] if index < len(clip_positions_ms) else None
            try:
                start_ms, end_ms = int(position[0]), int(position[1])
            except (TypeError, ValueError, IndexError):
                scheduled_positions = []
                break
            if start_ms < 0 or end_ms <= start_ms:
                scheduled_positions = []
                break
            scheduled_positions.append((start_ms, end_ms))
        if len(scheduled_positions) == len(paths):
            return self._build_scheduled_playlist(
                paths, output_path, transitions, durations_ms, scheduled_positions, clip_source_in_ms,
            )
        if len(paths) == 1:
            return paths[0]
        source_durations = [self._probe_duration_s(path) for path in paths]
        filters = []
        effective_durations = []
        for index in range(len(paths)):
            requested_clip = clip_durations_ms[index] if index < len(clip_durations_ms) else None
            clip_duration = min(source_durations[index], max(.05, float(requested_clip) / 1000)) if requested_clip else source_durations[index]
            effective_durations.append(clip_duration)
            filters.append(
                f'[{index}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={clip_duration:.3f},asetpts=N/SR/TB[music{index}]'
            )
        current = '[music0]'
        for index in range(1, len(paths)):
            transition = str(transitions[index - 1] if index - 1 < len(transitions) else 'CONSTANT_POWER').upper()
            requested_duration = max(0, min(10.0, float(
                durations_ms[index - 1] if index - 1 < len(durations_ms) else 2000
            ) / 1000))
            duration = max(0, min(
                requested_duration, effective_durations[index - 1] - .05, effective_durations[index] - .05,
            ))
            target = f'[music_mix{index}]'
            if transition == 'NONE' or duration == 0:
                filters.append(f'{current}[music{index}]concat=n=2:v=0:a=1{target}')
            else:
                filters.append(f'{current}[music{index}]acrossfade=d={duration:.3f}:c1=qsin:c2=qsin{target}')
            current = target
        self.runner.run([
            settings.FFMPEG_BINARY, '-y',
            *sum((['-i', str(path)] for path in paths), []),
            '-filter_complex', ';'.join(filters), '-map', current,
            '-c:a', 'pcm_s16le', str(output_path),
        ])
        return Path(output_path)

    def _build_scheduled_playlist(self, paths, output_path, transitions, durations_ms, positions_ms, source_in_ms=None):
        """Mix a playlist at the exact editorial positions from the timeline.

        A regular ``acrossfade`` concatenates inputs and therefore cannot
        represent a gap or a manually repositioned clip. Here every track is
        trimmed, delayed to its stored start and mixed on a common clock. For
        a real overlap, ``qsin`` fade curves preserve the constant-power hand
        off without changing the clips' absolute positions.
        """
        filters = []
        fade_ins = {}
        fade_outs = {}
        for index in range(1, len(paths)):
            if str(transitions[index - 1] if index - 1 < len(transitions) else 'CONSTANT_POWER').upper() != 'CONSTANT_POWER':
                continue
            previous_start, previous_end = positions_ms[index - 1]
            current_start, current_end = positions_ms[index]
            overlap_start, overlap_end = max(previous_start, current_start), min(previous_end, current_end)
            overlap_ms = max(0, overlap_end - overlap_start)
            try:
                requested_ms = int(durations_ms[index - 1] if index - 1 < len(durations_ms) else 2000)
            except (TypeError, ValueError):
                requested_ms = 2000
            fade_ms = min(max(0, requested_ms), overlap_ms)
            if fade_ms <= 0:
                continue
            # The sequence is ordered, so a transition into ``index`` fades
            # the preceding clip out exactly where their timeline overlap
            # begins. A later transition may replace its previous fade-out;
            # that is the least surprising behaviour for tightly packed clips.
            fade_outs[index - 1] = ((overlap_start - previous_start) / 1000, fade_ms / 1000)
            fade_ins[index] = ((overlap_start - current_start) / 1000, fade_ms / 1000)

        output_labels = []
        for index, _path in enumerate(paths):
            start_ms, end_ms = positions_ms[index]
            duration_s = max(.001, (end_ms - start_ms) / 1000)
            try:
                source_start_s = max(0, float((source_in_ms or [])[index]) / 1000)
            except (TypeError, ValueError, IndexError):
                source_start_s = 0
            chain = (
                f'[{index}:a]aresample=48000,aformat=channel_layouts=stereo,'
                f'atrim=start={source_start_s:.3f}:duration={duration_s:.3f},asetpts=N/SR/TB'
            )
            if index in fade_ins:
                fade_start, fade_duration = fade_ins[index]
                chain += f',afade=t=in:st={fade_start:.3f}:d={fade_duration:.3f}:curve=qsin'
            if index in fade_outs:
                fade_start, fade_duration = fade_outs[index]
                chain += f',afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}:curve=qsin'
            label = f'[scheduled_music{index}]'
            chain += f',adelay={start_ms}:all=1{label}'
            filters.append(chain)
            output_labels.append(label)
        filters.append(
            f'{"".join(output_labels)}amix=inputs={len(output_labels)}:duration=longest:'
            'dropout_transition=0:normalize=0[music_playlist]'
        )
        command = [settings.FFMPEG_BINARY, '-y']
        for path in paths:
            # Make a resized clip long enough to fill its saved duration. The
            # final atrim above remains the single source of truth.
            command.extend(['-stream_loop', '-1', '-i', str(path)])
        command.extend([
            '-filter_complex', ';'.join(filters), '-map', '[music_playlist]',
            '-c:a', 'pcm_s16le', str(output_path),
        ])
        self.runner.run(command)
        return Path(output_path)

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

    def measure_block_mean_volume_db(self, path: Path, block: SpeechBlock) -> float | None:
        try:
            start_s = max(0, int(block.start_ms)) / 1000
            duration_s = max(0.05, int(block.duration_ms)) / 1000
            result = self.runner.run_capture([
                settings.FFMPEG_BINARY, '-ss', f'{start_s:.3f}', '-t', f'{duration_s:.3f}',
                '-i', FFmpegRunner.input_arg(path), '-vn', '-af', 'volumedetect', '-f', 'null', '-',
            ])
        except Exception:
            logger.warning('Não foi possível medir o volume da fala no bloco %sms-%sms.', block.start_ms, block.end_ms)
            return None
        return parse_mean_volume_db(result.stderr)

    @staticmethod
    def estimate_block_duck_db(music_mean_db, voice_mean_db, music_volume, settings_: DuckingSettings) -> float:
        """Compute attenuation required for this voice block's measured level."""
        if music_mean_db is None or voice_mean_db is None:
            return settings_.base_duck_db
        music_bed_db = music_mean_db + (20 * math.log10(max(0.0001, float(music_volume))))
        desired_music_db = voice_mean_db - settings_.target_voice_to_music_gap_db
        return clamp(
            music_bed_db - desired_music_db,
            settings_.min_duck_db,
            settings_.max_duck_db,
        )

    def _dynamic_duck_dbs(self, video_path, music_mean_db, speech_blocks, music_volume, settings_):
        measured = [
            self.measure_block_mean_volume_db(video_path, block)
            for block in speech_blocks[:MAX_DYNAMIC_DUCK_BLOCKS]
        ]
        measured_dbs = [
            0.0 if voice_db is not None and voice_db < settings_.voice_activation_db
            else self.estimate_block_duck_db(music_mean_db, voice_db, music_volume, settings_)
            for voice_db in measured
        ]
        fallback = (
            sorted(measured_dbs)[len(measured_dbs) // 2]
            if measured_dbs else settings_.base_duck_db
        )
        return [*measured_dbs, *([fallback] * (len(speech_blocks) - len(measured_dbs)))], measured

    def mix(
        self, video_path: Path, music_path: Path, output_path: Path, *,
        music_volume: float, duration_ms: int, speech_blocks=None, protected_ranges=None,
        settings_: DuckingSettings = None, ducking_enabled: bool = True, spectral_enabled: bool = False,
        music_keyframes=None,
    ) -> AudioMixResult:
        self._validate_music_input(music_path)
        settings_ = settings_ or DuckingSettings()
        # Re-group the cue-derived blocks here as a final safeguard.  A 1s pause
        # or breath belongs to the same spoken segment, so the music stays ducked
        # instead of rising and falling between consecutive phrases.
        speech_blocks = group_speech_blocks(
            [(block.start_ms, block.end_ms) for block in (speech_blocks or [])],
            gap_threshold_ms=max(0, int(settings_.speech_gap_hold_ms)),
        )
        metrics = {
            'speech_block_count': len(speech_blocks),
            'protected_range_count': len(protected_ranges or []),
            'speech_gap_hold_ms': settings_.speech_gap_hold_ms,
        }
        if not ducking_enabled or not speech_blocks or duration_ms <= 0:
            loop_metrics = self._mix_flat(
                video_path, music_path, output_path, music_volume, duration_ms=duration_ms,
                settings_=settings_, music_keyframes=music_keyframes,
            )
            metrics['duck_db'] = 0.0
            metrics['mode'] = 'flat'
            metrics.update(loop_metrics)
            return AudioMixResult(output_path, metrics)

        music_mean_db = self.measure_mean_volume_db(music_path)
        voice_mean_db = self.measure_mean_volume_db(video_path)
        duck_dbs, voice_block_levels = self._dynamic_duck_dbs(
            video_path, music_mean_db, speech_blocks, music_volume, settings_,
        )
        duck_gains = [linear_gain_from_db(-duck_db) for duck_db in duck_dbs]
        envelope = build_dynamic_ducking_envelope(speech_blocks, duration_ms, duck_gains, settings_)
        manual_envelope = normalize_volume_keyframes(music_keyframes, duration_ms)
        envelope = combine_gain_envelopes(envelope, manual_envelope)
        volume_expression = build_piecewise_expression(envelope)

        spectral_density = 0.0
        spectral_windows = []
        if spectral_enabled:
            spectral_density = self.estimate_spectral_density(music_path, settings_)
            cut_db = spectral_density * settings_.spectral_max_cut_db
            spectral_windows = build_spectral_windows(speech_blocks, cut_db)

        loop_filters, loop_label, loop_metrics = self._build_music_loop_filters(music_path, duration_ms)
        duration_s = max(0.001, duration_ms / 1000)
        filters = [
            # The dialogue stream can be a few frames shorter than the video after
            # concatenation/cuts. Pad it to the explicit master duration so amix
            # never makes the music bed disappear near the end of the picture.
            # Regenerate audio timestamps before padding.  Normalized source clips
            # can carry fractional timestamps; preserving them here can accumulate
            # into an audible lip-sync drift near the end of a long render.
            f'[0:a]volume={linear_gain_from_db(settings_.voice_gain_db):.4f},'
            f'aresample=async=1:first_pts=0,apad=whole_dur={duration_s:.3f},'
            f'atrim=duration={duration_s:.3f}[dialogue]',
            *loop_filters,
            f'{loop_label}volume={float(music_volume):.3f}[music_base]',
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
        filters.append(
            f'[dialogue]{music_label}amix=inputs=2:duration=longest:dropout_transition=0,'
            f'atrim=duration={duration_s:.3f}[a]'
        )

        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(video_path),
            '-stream_loop', '-1', '-i', str(music_path), '-filter_complex', ';'.join(filters),
            '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', str(output_path),
        ])
        metrics.update({
            'mode': 'adaptive',
            'music_mean_db': music_mean_db,
            'voice_mean_db': voice_mean_db,
            'duck_db': round(sum(duck_dbs) / max(1, len(duck_dbs)), 2),
            'duck_db_by_block': [round(value, 2) for value in duck_dbs],
            'voice_block_levels_db': [round(value, 2) if value is not None else None for value in voice_block_levels],
            'target_voice_to_music_gap_db': settings_.target_voice_to_music_gap_db,
            'between_speech_gain': settings_.between_speech_gain,
            'voice_activation_db': settings_.voice_activation_db,
            'voice_gain_db': settings_.voice_gain_db,
            'manual_music_keyframe_count': len(music_keyframes or []),
            'spectral_applied': bool(spectral_windows),
            'spectral_density': round(spectral_density, 2) if spectral_enabled else None,
            **loop_metrics,
        })
        return AudioMixResult(output_path, metrics)

    def _probe_duration_s(self, music_path: Path) -> float:
        """Returns the track duration so its repetitions can overlap cleanly."""
        try:
            value = self.runner.run([
                settings.FFPROBE_BINARY, '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(music_path),
            ])
        except ExternalMediaError as exc:
            raise ExternalMediaError(
                'Não foi possível identificar a duração da música de fundo do template.'
            ) from exc
        try:
            duration_s = float(value.strip())
        except (TypeError, ValueError):
            duration_s = 0.0
        if duration_s <= 0:
            raise ExternalMediaError(
                'A música de fundo do template não possui uma duração válida.'
            )
        return duration_s

    def _build_music_loop_filters(self, music_path: Path, duration_ms: int):
        """Builds a music bed trimmed to the master duration.

        The input itself is looped with FFmpeg's native ``-stream_loop -1``. A fixed
        `asplit`/`acrossfade` graph grows with the video duration and can leave the
        final part of long renders without a music bed.
        """
        duration_s = max(0.001, duration_ms / 1000)
        return self._music_loop_plan(duration_s, self._probe_duration_s(music_path))

    @staticmethod
    def _music_loop_plan(video_duration_s: float, music_duration_s: float = 0.0):
        if music_duration_s <= 0:
            return [], '[1:a]', {'music_loop_count': 1, 'music_crossfade_s': 0.0}
        loop_count = max(1, int(math.ceil(video_duration_s / music_duration_s)))
        return [f'[1:a]asetpts=N/SR/TB,atrim=duration={video_duration_s:.3f}[music_looped]'], '[music_looped]', {
            'music_loop_count': loop_count,
            'music_crossfade_s': 0.0,
        }

    def _validate_music_input(self, music_path: Path):
        """Fail early with an actionable message when the template track is corrupt.

        A file can exist in the configured storage while containing an interrupted
        upload (or even text saved with an audio extension).  Without this check,
        FFmpeg only reports a generic render error after the full video has already
        been assembled.
        """
        path = Path(music_path)
        if not path.is_file() or path.stat().st_size < 1024:
            raise ExternalMediaError(
                'A música de fundo do template está vazia ou inválida. '
                'Envie novamente um arquivo de áudio válido no template.'
            )
        try:
            stream_type = self.runner.run([
                settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'a:0',
                '-show_entries', 'stream=codec_type', '-of', 'default=noprint_wrappers=1:nokey=1',
                str(path),
            ]).strip()
        except ExternalMediaError as exc:
            raise ExternalMediaError(
                'A música de fundo do template não pôde ser lida. '
                'Envie novamente um arquivo de áudio válido no template.'
            ) from exc
        if stream_type != 'audio':
            raise ExternalMediaError(
                'A música de fundo do template não possui uma faixa de áudio válida. '
                'Envie novamente um arquivo de áudio válido no template.'
            )

    def _mix_flat(
        self, video_path, music_path, output_path, music_volume, *, duration_ms,
        settings_=None, music_keyframes=None,
    ):
        settings_ = settings_ or DuckingSettings()
        loop_filters, loop_label, loop_metrics = self._build_music_loop_filters(music_path, duration_ms)
        duration_s = max(0.001, duration_ms / 1000)
        manual_expression = build_piecewise_expression(
            normalize_volume_keyframes(music_keyframes, duration_ms),
        )
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(video_path),
            '-stream_loop', '-1', '-i', str(music_path), '-filter_complex',
            ';'.join([
                f'[0:a]volume={linear_gain_from_db(settings_.voice_gain_db):.4f},'
                f'aresample=async=1:first_pts=0,apad=whole_dur={duration_s:.3f},'
                f'atrim=duration={duration_s:.3f}[dialogue]',
                *loop_filters,
                f"{loop_label}volume={float(music_volume):.3f},volume=eval=frame:volume='{manual_expression}'[music]",
                f'[dialogue][music]amix=inputs=2:duration=longest:dropout_transition=0,'
                f'atrim=duration={duration_s:.3f}[a]',
            ]),
            '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', str(output_path),
        ])
        return loop_metrics
