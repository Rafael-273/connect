from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from .audio_mixing import clip_blocks_against_protected_ranges
from .audio_utils import (
    build_piecewise_expression, clamp, linear_gain_from_db, parse_mean_volume_db,
    settings_from_config,
)
from .ffmpeg_runner import FFmpegRunner

logger = logging.getLogger(__name__)

# Safety cap on how many speech blocks get their own loudness measurement/correction.
# Beyond this, the filter graph would grow too large for very long videos, so leveling
# is skipped for the overflow blocks (they keep whatever level they already have).
MAX_LEVELING_BLOCKS = 60

# Corrections smaller than this are inaudible in practice and only add filter-graph
# complexity, so they're treated as "already consistent" and skipped.
MIN_LEVELING_GAIN_DB = 0.2


@dataclass(frozen=True)
class DialogueSettings:
    """Tunable knobs for `DialogueProcessor`. Every stage is individually toggleable so
    the admin can disable whichever ones aren't needed for a given template/recording.
    """

    highpass_enabled: bool = True
    highpass_hz: int = 80
    eq_enabled: bool = True
    eq_low_cut_hz: int = 300
    eq_low_cut_db: float = -2.0
    eq_presence_hz: int = 4000
    eq_presence_db: float = 1.5
    compression_enabled: bool = True
    compression_threshold_db: float = -18.0
    compression_ratio: float = 2.5
    compression_attack_ms: int = 15
    compression_release_ms: int = 200
    # De-essing can introduce audible artifacts on some voices/mics, so it defaults to
    # off ("usar apenas quando necessários") and is meant to be enabled per-template.
    deesser_enabled: bool = False
    leveling_enabled: bool = True
    leveling_max_gain_db: float = 6.0

    @classmethod
    def from_config(cls, config):
        return settings_from_config(cls, config)


@dataclass
class DialogueProcessResult:
    path: Path
    metrics: dict = field(default_factory=dict)


def build_leveling_envelope(block_gains, duration_ms, transition_ms=120):
    """Builds (time_seconds, gain) keyframes that ease each speech block towards its own
    corrective gain and settle back to neutral (1.0 / no change) outside speech.

    `block_gains` is an iterable of (block, gain) pairs, where `block` exposes
    `start_ms`/`end_ms`. Mirrors `build_ducking_envelope`'s attack/release shape (short
    symmetric transitions, never overlapping into the next block) but each block can
    target a different gain instead of one shared duck level.
    """
    duration_s = max(0.001, duration_ms / 1000)
    transition_s = max(0.01, transition_ms / 1000)
    keyframes = [(0.0, 1.0)]
    ordered = sorted(block_gains, key=lambda item: item[0].start_ms)
    for index, (block, gain) in enumerate(ordered):
        start_s = block.start_ms / 1000
        end_s = block.end_ms / 1000
        attack_end_s = min(end_s, start_s + transition_s)
        next_start_s = ordered[index + 1][0].start_ms / 1000 if index + 1 < len(ordered) else duration_s
        release_end_s = min(end_s + transition_s, next_start_s)
        keyframes.append((start_s, 1.0))
        keyframes.append((attack_end_s, gain))
        keyframes.append((end_s, gain))
        keyframes.append((max(end_s, release_end_s), 1.0))
    keyframes.append((duration_s, 1.0))
    deduped = []
    for point in keyframes:
        if deduped and abs(point[0] - deduped[-1][0]) < 0.0005:
            deduped[-1] = point
        else:
            deduped.append(point)
    return deduped


class DialogueProcessor:
    """Cleans up and balances the dialogue track before it is mixed with music.

    Two independent, individually-toggleable stages:

    - a static enhancement chain (high-pass cleanup, corrective EQ, moderate
      compression, optional de-esser) applied uniformly across the whole track;
    - per-Speech-Block loudness leveling, which nudges each block towards the
      *median* loudness of all the blocks (not a fixed absolute target), so
      different speakers/takes/mics end up sounding consistent with each other
      rather than being forced to some arbitrary studio reference.

    Follows the "prefer preserving audio" principle: leveling gain is capped by
    `leveling_max_gain_db`, protected ranges are left untouched, and when fewer than
    two speech blocks are available (or loudness can't be measured) leveling makes no
    change at all rather than guessing.
    """

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    @staticmethod
    def _static_chain(settings_: DialogueSettings) -> list[str]:
        chain = []
        if settings_.highpass_enabled:
            chain.append(f'highpass=f={settings_.highpass_hz}')
        if settings_.eq_enabled:
            chain.append(
                f'equalizer=f={settings_.eq_low_cut_hz}:width_type=o:width=1.0:'
                f'g={settings_.eq_low_cut_db:.2f}'
            )
            chain.append(
                f'equalizer=f={settings_.eq_presence_hz}:width_type=o:width=1.5:'
                f'g={settings_.eq_presence_db:.2f}'
            )
        if settings_.compression_enabled:
            chain.append(
                f'acompressor=threshold={settings_.compression_threshold_db}dB:'
                f'ratio={settings_.compression_ratio}:attack={settings_.compression_attack_ms}:'
                f'release={settings_.compression_release_ms}:makeup=1'
            )
        if settings_.deesser_enabled:
            chain.append('deesser')
        return chain

    def measure_block_mean_db(self, path: Path, block) -> float | None:
        try:
            start = max(0, int(block.start_ms)) / 1000
            duration = max(0.001, int(block.end_ms - block.start_ms) / 1000)
            result = self.runner.run_capture([
                # There may be dozens of speech blocks. Seeking first avoids
                # decoding the whole video from zero for every measurement.
                settings.FFMPEG_BINARY, '-ss', f'{start:.3f}', '-t', f'{duration:.3f}', '-i', str(path),
                '-vn', '-af', 'volumedetect',
                '-f', 'null', '-',
            ])
        except Exception:
            logger.warning(
                'Não foi possível medir o volume do bloco de fala (%sms-%sms).',
                block.start_ms, block.end_ms, exc_info=True,
            )
            return None
        return parse_mean_volume_db(result.stderr)

    def _compute_block_gains(self, video_path: Path, speech_blocks, protected_ranges, settings_):
        blocks = clip_blocks_against_protected_ranges(speech_blocks or [], protected_ranges or [])
        if len(blocks) < 2:
            return [], None
        candidates = sorted(blocks, key=lambda item: item.start_ms)[:MAX_LEVELING_BLOCKS]
        levels = [(block, self.measure_block_mean_db(video_path, block)) for block in candidates]
        measured = [db for _, db in levels if db is not None]
        if len(measured) < 2:
            return [], None
        reference_db = statistics.median(measured)
        block_gains = []
        for block, db in levels:
            if db is None:
                continue
            gain_db = clamp(reference_db - db, -settings_.leveling_max_gain_db, settings_.leveling_max_gain_db)
            if abs(gain_db) >= MIN_LEVELING_GAIN_DB:
                block_gains.append((block, gain_db))
        return block_gains, reference_db

    def process(
        self, video_path: Path, output_path: Path, *,
        duration_ms: int, speech_blocks=None, protected_ranges=None,
        settings_: DialogueSettings = None,
    ) -> DialogueProcessResult:
        settings_ = settings_ or DialogueSettings()
        static_chain = self._static_chain(settings_)
        metrics = {
            'highpass_enabled': settings_.highpass_enabled,
            'eq_enabled': settings_.eq_enabled,
            'compression_enabled': settings_.compression_enabled,
            'deesser_enabled': settings_.deesser_enabled,
            'leveling_applied': False,
            'leveling_block_count': 0,
        }

        block_gains, reference_db = ([], None)
        if settings_.leveling_enabled and duration_ms > 0:
            block_gains, reference_db = self._compute_block_gains(
                video_path, speech_blocks, protected_ranges, settings_,
            )

        if not static_chain and not block_gains:
            self._copy(video_path, output_path)
            return DialogueProcessResult(output_path, metrics)

        if static_chain:
            filters = [f"[0:a]{','.join(static_chain)}[dlg]"]
        else:
            filters = ['[0:a]anull[dlg]']
        current_label = 'dlg'
        if block_gains:
            envelope = build_leveling_envelope(
                [(block, linear_gain_from_db(gain_db)) for block, gain_db in block_gains], duration_ms,
            )
            expression = build_piecewise_expression(envelope)
            filters.append(f"[{current_label}]volume=eval=frame:volume='{expression}'[leveled]")
            current_label = 'leveled'

        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(video_path), '-filter_complex', ';'.join(filters),
            '-map', '0:v?', '-map', f'[{current_label}]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', str(output_path),
        ])
        gains = [gain_db for _, gain_db in block_gains]
        metrics.update({
            'leveling_applied': bool(block_gains),
            'leveling_block_count': len(block_gains),
            'leveling_reference_db': round(reference_db, 2) if reference_db is not None else None,
            'leveling_gain_range_db': [round(min(gains), 2), round(max(gains), 2)] if gains else None,
        })
        return DialogueProcessResult(output_path, metrics)
    def _copy(self, video_path, output_path):
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(video_path), '-c', 'copy', str(output_path),
        ])


# Nome público usado pela arquitetura/orquestrador. O alias preserva compatibilidade
# com o pipeline já existente, que nasceu com o nome curto DialogueProcessor.
DialogueProcessingService = DialogueProcessor
