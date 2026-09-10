from __future__ import annotations

from .audio_analysis import AudioAnalysisService


class AudioValidationService:
    def __init__(self, analysis=None):
        self.analysis = analysis or AudioAnalysisService()

    def validate(self, path, profile) -> dict:
        metrics = self.analysis.analyze(path)
        loudness = metrics.get('integrated_lufs')
        peak = metrics.get('true_peak_dbtp')
        integrity_ok = loudness is not None and peak is not None
        loudness_ok = loudness is not None and abs(loudness - float(profile.target_lufs)) <= 2.0
        peak_ok = peak is not None and peak <= float(profile.true_peak_db) + 0.2
        metrics.update({
            'loudness_compliant': loudness_ok,
            'true_peak_compliant': peak_ok,
            'integrity_ok': integrity_ok,
            'is_valid': integrity_ok and peak_ok and not metrics.get('clipping_detected'),
        })
        return metrics
