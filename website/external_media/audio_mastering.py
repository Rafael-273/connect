from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from .audio_utils import linear_gain_from_db
from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MasteringTarget:
    target_lufs: float = -16.0
    true_peak_db: float = -1.0
    bus_compression_enabled: bool = False
    limiter_enabled: bool = True
    profile_code: str = ''
    profile_name: str = ''
    dynamic_range_target: float = 11.0
    low_frequency_control_db: float = 0.0
    high_frequency_control_db: float = 0.0
    max_gain_db: float = 12.0
    max_limiter_reduction_db: float = 4.0

    @classmethod
    def from_profile(cls, profile):
        if not profile:
            return cls()
        return cls(
            target_lufs=float(profile.target_lufs),
            true_peak_db=float(profile.true_peak_db),
            bus_compression_enabled=bool(profile.bus_compression_enabled),
            limiter_enabled=bool(profile.limiter_enabled),
            profile_code=profile.code,
            profile_name=profile.name,
            dynamic_range_target=float(getattr(profile, 'dynamic_range_target', 11.0)),
            low_frequency_control_db=float(getattr(profile, 'low_frequency_control', 0.0)),
            high_frequency_control_db=float(getattr(profile, 'high_frequency_control', 0.0)),
            max_gain_db=float(getattr(profile, 'max_gain_db', 12.0)),
            max_limiter_reduction_db=float(getattr(profile, 'max_limiter_reduction_db', 4.0)),
        )


@dataclass
class AudioMasterResult:
    path: Path
    metrics: dict = field(default_factory=dict)


class AudioMasteringService:
    """Applies loudness normalization + true-peak safety to a final stereo mix.

    Operates only on the finished mix (no knowledge of individual dialogue/music
    elements), following a conservative two-pass `loudnorm` approach: measure first,
    then apply a (mostly linear) gain correction so the sound isn't reshaped more than
    necessary to hit the target — matching the "prefer preserving audio" principle.
    """

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def master(self, input_path: Path, output_path: Path, target: MasteringTarget) -> AudioMasterResult:
        measured = self._measure(input_path, target)
        effective_target = self._effective_target(target, measured)
        filters = self._filter_chain(target, measured, effective_target)
        try:
            self.runner.run([
                settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(input_path),
                '-map', '0:v?', '-map', '0:a', '-c:v', 'copy',
                '-af', ','.join(filters), '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart', str(output_path),
            ])
        except ExternalMediaError:
            logger.warning('Masterização falhou; mantendo o mix sem masterizar.', exc_info=True)
            return AudioMasterResult(input_path, {'applied': False, 'error': True})
        metrics = self._result_metrics(measured, target, effective_target)
        return AudioMasterResult(output_path, metrics)

    def master_audio(self, input_path: Path, output_path: Path, target: MasteringTarget) -> AudioMasterResult:
        """Masteriza somente o mix estéreo; mux de vídeo pertence ao AudioMuxingService."""
        measured = self._measure(input_path, target)
        effective_target = self._effective_target(target, measured)
        filters = self._filter_chain(target, measured, effective_target)
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(input_path),
            '-vn', '-af', ','.join(filters), '-c:a', 'pcm_s24le', str(output_path),
        ])
        return AudioMasterResult(output_path, self._result_metrics(measured, target, effective_target))

    @staticmethod
    def _effective_target(target, measured):
        """Limita ganho/limiting: qualidade e dinâmica têm prioridade sobre o LUFS alvo."""
        try:
            input_lufs = float(measured['input_i'])
            input_peak = float(measured['input_tp'])
        except (KeyError, TypeError, ValueError):
            return target.target_lufs
        wanted_gain = target.target_lufs - input_lufs
        if wanted_gain <= 0:
            # Atenuação não exige limiting e é segura; áudios já altos podem chegar ao alvo.
            return target.target_lufs
        peak_safe_gain = target.true_peak_db - input_peak + target.max_limiter_reduction_db
        allowed_gain = min(wanted_gain, target.max_gain_db, max(0.0, peak_safe_gain))
        return min(target.target_lufs, input_lufs + allowed_gain)

    def _filter_chain(self, target, measured, effective_target):
        filters = []
        if abs(target.low_frequency_control_db) >= 0.1:
            filters.append(f'bass=f=120:g={target.low_frequency_control_db:.1f}')
        if abs(target.high_frequency_control_db) >= 0.1:
            filters.append(f'treble=f=7000:g={target.high_frequency_control_db:.1f}')
        if target.bus_compression_enabled:
            filters.append('acompressor=threshold=-18dB:ratio=2:attack=20:release=250:makeup=1')
        filters.append(self._loudnorm_filter(target, measured, effective_target))
        if target.limiter_enabled:
            limit = max(0.0001, min(1.0, linear_gain_from_db(target.true_peak_db)))
            filters.append(f'alimiter=limit={limit:.4f}:attack=5:release=50')
        return filters

    def _measure(self, input_path: Path, target: MasteringTarget) -> dict:
        try:
            result = self.runner.run_capture([
                settings.FFMPEG_BINARY, '-i', FFmpegRunner.input_arg(input_path), '-af',
                f'loudnorm=I={target.target_lufs}:TP={target.true_peak_db}:LRA=11:print_format=json',
                '-f', 'null', '-',
            ])
        except ExternalMediaError:
            logger.warning('Não foi possível medir loudness antes da masterização.', exc_info=True)
            return {}
        return self._parse_loudnorm_json(result.stderr)

    @staticmethod
    def _parse_loudnorm_json(stderr_text: str) -> dict:
        matches = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', stderr_text, re.DOTALL)
        if not matches:
            return {}
        try:
            return json.loads(matches[-1])
        except json.JSONDecodeError:
            logger.warning('Não foi possível interpretar as métricas de loudness do ffmpeg.')
            return {}

    @staticmethod
    def _loudnorm_filter(target: MasteringTarget, measured: dict, effective_target=None) -> str:
        loudness = target.target_lufs if effective_target is None else effective_target
        base = f'loudnorm=I={loudness}:TP={target.true_peak_db}:LRA={target.dynamic_range_target}'
        required = {'input_i', 'input_tp', 'input_lra', 'input_thresh'}
        if not required.issubset(measured):
            return base
        try:
            offset = measured.get('target_offset', 0) if abs(loudness - target.target_lufs) < 0.05 else 0
            return (
                f'{base}:measured_I={measured["input_i"]}:measured_TP={measured["input_tp"]}:'
                f'measured_LRA={measured["input_lra"]}:measured_thresh={measured["input_thresh"]}:'
                f'offset={offset}:linear=true'
            )
        except (KeyError, TypeError, ValueError):
            return base

    @staticmethod
    def _result_metrics(measured: dict, target: MasteringTarget, effective_target=None) -> dict:
        def to_float(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        input_i = to_float(measured.get('input_i'))
        input_tp = to_float(measured.get('input_tp'))
        achieved_target = target.target_lufs if effective_target is None else effective_target
        gain = round(achieved_target - input_i, 2) if input_i is not None else None
        predicted_peak = input_tp + gain if input_tp is not None and gain is not None else None
        limiter_reduction = max(0.0, predicted_peak - target.true_peak_db) if predicted_peak is not None else None
        return {
            'applied': True,
            'profile': target.profile_code,
            'target_lufs': target.target_lufs,
            'target_true_peak_db': target.true_peak_db,
            'loudness_before_lufs': input_i,
            'true_peak_before_dbtp': input_tp,
            'loudness_after_lufs': achieved_target if input_i is not None else None,
            'gain_applied_db': gain,
            'limiter_gain_reduction_db': round(limiter_reduction, 2) if limiter_reduction is not None else None,
            'target_reduced_to_preserve_quality': achieved_target < target.target_lufs - 0.05,
            'bus_compression_enabled': target.bus_compression_enabled,
            'limiter_enabled': target.limiter_enabled,
        }
