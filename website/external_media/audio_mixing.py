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
    keyframes = [(0.0, 1.0)]
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
            keyframes.append((start_s, 1.0))
            keyframes.append((attack_end_s, duck_gain))
        else:
            keyframes.append((start_s, duck_gain))
        keyframes.append((end_s, duck_gain))
        if not continues_into_next_block and end_s < duration_s:
            keyframes.append((min(end_s + release_s, duration_s), 1.0))
    keyframes.append((duration_s, 1.0))
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
        return [(0.0, 1.0), (max(0.001, duration_ms / 1000), 1.0)]
    duration_s = max(0.001, duration_ms / 1000)
    attack_s = max(0.01, settings_.attack_ms / 1000)
    release_s = max(0.01, settings_.release_ms / 1000)
    keyframes = [(0.0, 1.0)]
    ordered = list(zip(sorted(blocks, key=lambda item: item.start_ms), duck_gains))
    previous_end = None
    previous_gain = 1.0
    for index, (block, duck_gain) in enumerate(ordered):
        start_s = max(0.0, block.start_ms / 1000)
        end_s = min(duration_s, block.end_ms / 1000)
        if end_s <= start_s:
            continue
        next_start_s = ordered[index + 1][0].start_ms / 1000 if index + 1 < len(ordered) else duration_s
        previous_release_end = (previous_end + release_s) if previous_end is not None else -1
        if previous_end is None or start_s > previous_release_end:
            keyframes.append((start_s, 1.0))
        else:
            # Keep the bed under speech across short pauses, transitioning to the
            # next block's measured target instead of briefly rising to full level.
            keyframes.append((start_s, previous_gain))
        keyframes.append((min(end_s, start_s + attack_s), duck_gain))
        keyframes.append((end_s, duck_gain))
        if next_start_s - end_s > release_s and end_s < duration_s:
            keyframes.append((min(duration_s, end_s + release_s), 1.0))
        previous_end = end_s
        previous_gain = duck_gain
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
            self.estimate_block_duck_db(music_mean_db, voice_db, music_volume, settings_)
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
            f'[0:a]aresample=async=1:first_pts=0,apad=whole_dur={duration_s:.3f},'
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
            '-i', str(music_path), '-filter_complex', ';'.join(filters),
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
        """Builds a finite music bed whose loop points are crossfaded.

        ``-stream_loop`` repeats the source with a hard cut.  Creating explicit audio
        copies lets ``acrossfade`` overlap the tail and beginning of the track, which
        avoids a noticeable restart and guarantees music remains under the final block.
        """
        duration_s = max(0.001, duration_ms / 1000)
        return self._music_loop_plan(duration_s, self._probe_duration_s(music_path))

    @staticmethod
    def _music_loop_plan(video_duration_s: float, music_duration_s: float = 0.0):
        if music_duration_s <= 0:
            # The caller replaces this placeholder after probing. This avoids creating
            # a malformed filter graph if a storage backend reports no duration.
            return [], '[1:a]', {'music_loop_count': 1, 'music_crossfade_s': 0.0}
        crossfade_s = min(1.25, music_duration_s / 4)
        crossfade_s = max(0.08, crossfade_s)
        loop_step_s = max(0.01, music_duration_s - crossfade_s)
        loop_count = max(1, int(math.ceil((video_duration_s - music_duration_s) / loop_step_s)) + 1)
        labels = [f'music_loop{index}' for index in range(loop_count)]
        split_outputs = ''.join(f'[{label}]' for label in labels)
        filters = [
            f'[1:a]atrim=duration={music_duration_s:.3f},asetpts=N/SR/TB,'
            f'asplit={loop_count}{split_outputs}',
        ]
        current_label = labels[0]
        for index, label in enumerate(labels[1:], start=1):
            next_label = f'music_cross{index}'
            filters.append(
                f'[{current_label}][{label}]acrossfade=d={crossfade_s:.3f}:c1=tri:c2=tri[{next_label}]'
            )
            current_label = next_label
        final_label = 'music_looped'
        filters.append(f'[{current_label}]atrim=duration={video_duration_s:.3f}[{final_label}]')
        return filters, f'[{final_label}]', {
            'music_loop_count': loop_count,
            'music_crossfade_s': round(crossfade_s, 3) if loop_count > 1 else 0.0,
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

    def _mix_flat(self, video_path, music_path, output_path, music_volume, *, duration_ms):
        loop_filters, loop_label, loop_metrics = self._build_music_loop_filters(music_path, duration_ms)
        duration_s = max(0.001, duration_ms / 1000)
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(video_path), '-i', str(music_path), '-filter_complex',
            ';'.join([
                f'[0:a]aresample=async=1:first_pts=0,apad=whole_dur={duration_s:.3f},'
                f'atrim=duration={duration_s:.3f}[dialogue]',
                *loop_filters,
                f'{loop_label}volume={float(music_volume):.3f}[music]',
                f'[dialogue][music]amix=inputs=2:duration=longest:dropout_transition=0,'
                f'atrim=duration={duration_s:.3f}[a]',
            ]),
            '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', str(output_path),
        ])
        return loop_metrics
