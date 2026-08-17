from __future__ import annotations

import json
import re
from pathlib import Path

from django.conf import settings

from .ffmpeg_runner import FFmpegRunner


class AudioAnalysisService:
    """Mede tecnicamente um mix final sem alterá-lo."""

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def analyze(self, input_path: Path) -> dict:
        result = self.runner.run_capture([
            settings.FFMPEG_BINARY, '-hide_banner', '-i', str(input_path),
            '-af', 'loudnorm=I=-16:TP=-1:LRA=11:print_format=json', '-f', 'null', '-',
        ])
        data = self._parse(result.stderr)
        loudness = self._number(data.get('input_i'))
        true_peak = self._number(data.get('input_tp'))
        lra = self._number(data.get('input_lra'))
        threshold = self._number(data.get('input_thresh'))
        clipping = true_peak is not None and true_peak >= -0.05
        metrics = {
            'integrated_lufs': loudness,
            'true_peak_dbtp': true_peak,
            'loudness_range_lu': lra,
            'threshold_lufs': threshold,
            'dynamic_range': self._dynamic_range_label(lra),
            'clipping_detected': clipping,
        }
        metrics['diagnostics'] = self._diagnostics(metrics)
        return metrics

    @staticmethod
    def _parse(stderr):
        matches = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', stderr, re.DOTALL)
        if not matches:
            return {}
        try:
            return json.loads(matches[-1])
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _number(value):
        try:
            number = float(value)
            return number if number not in {float('inf'), float('-inf')} else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _dynamic_range_label(lra):
        if lra is None:
            return 'Não disponível'
        if lra < 5:
            return 'Comprimida'
        if lra <= 12:
            return 'Normal'
        return 'Ampla'

    @staticmethod
    def _diagnostics(metrics):
        diagnostics = []
        loudness = metrics['integrated_lufs']
        peak = metrics['true_peak_dbtp']
        if loudness is not None and loudness < -21:
            diagnostics.append('Volume geral baixo')
        if loudness is not None and loudness > -10:
            diagnostics.append('Volume geral muito alto')
        if metrics['clipping_detected']:
            diagnostics.append('Possível clipping')
        if metrics['dynamic_range'] == 'Ampla':
            diagnostics.append('Dinâmica excessiva para reprodução digital')
        if not diagnostics and peak is not None:
            diagnostics.append('Áudio já bem equilibrado')
        return diagnostics

