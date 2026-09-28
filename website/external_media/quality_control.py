from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from .audio_analysis import AudioAnalysisService
from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner


@dataclass
class QualityReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self):
        return not self.errors

    def as_dict(self):
        return {'ok': self.ok, 'errors': self.errors, 'warnings': self.warnings, 'metrics': self.metrics}

    def require_ok(self):
        if self.errors:
            raise ExternalMediaError('Controle de qualidade: ' + ' '.join(self.errors))


class MediaQualityService:
    """Checks final media structure, timing, subtitles and audio health."""

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def validate_media(self, path: Path, *, expected_duration_ms=None, deep_audio=False):
        report = QualityReport()
        probe = self._probe(path)
        streams = probe.get('streams') or []
        has_video = any(item.get('codec_type') == 'video' for item in streams)
        has_audio = any(item.get('codec_type') == 'audio' for item in streams)
        duration_ms = self._duration_ms(probe)
        report.metrics.update({
            'duration_ms': duration_ms,
            'has_video': has_video,
            'has_audio': has_audio,
        })
        if not has_video:
            report.errors.append('O arquivo final não possui faixa de vídeo.')
        if not has_audio:
            report.errors.append('O arquivo final não possui faixa de áudio.')
        if duration_ms <= 0:
            report.errors.append('A duração do arquivo final é inválida.')
        if expected_duration_ms and duration_ms:
            tolerance = max(1000, round(expected_duration_ms * 0.01))
            drift_ms = abs(duration_ms - int(expected_duration_ms))
            report.metrics['expected_duration_ms'] = int(expected_duration_ms)
            report.metrics['duration_drift_ms'] = drift_ms
            if drift_ms > tolerance:
                report.errors.append(
                    f'A duração final divergiu {drift_ms / 1000:.2f}s do planejamento.'
                )
        if deep_audio and has_audio:
            metrics = AudioAnalysisService(self.runner).analyze(path)
            report.metrics['audio'] = metrics
            if metrics.get('integrated_lufs') is None:
                report.errors.append('Não foi possível medir o áudio final.')
            if metrics.get('clipping_detected'):
                report.warnings.append('Foram detectados possíveis picos de áudio no limite digital.')
            long_silences = self._long_silences(path)
            report.metrics['long_silences'] = long_silences
            if long_silences:
                report.warnings.append(
                    f'{len(long_silences)} trecho(s) com mais de 8s de silêncio devem ser revisados.'
                )
        return report

    def validate_preview_proxy(
        self, path: Path, *, expected_duration_ms=None, expected_width=None, expected_height=None,
    ):
        """Verify the clock used by an interactive source proxy.

        A proxy can have a valid container duration while AAC packets still
        start seconds after its video stream.  Browsers then show a silent
        beginning and the editor's caption clock appears wrong.  The preview
        must reject that asset before it is cached as READY.
        """
        report = QualityReport()
        probe = self._probe(path)
        streams = probe.get('streams') or []
        video = next((item for item in streams if item.get('codec_type') == 'video'), None)
        audio = next((item for item in streams if item.get('codec_type') == 'audio'), None)
        duration_ms = self._duration_ms(probe)
        report.metrics['duration_ms'] = duration_ms
        if not video:
            report.errors.append('O proxy não possui faixa de vídeo.')
            return report
        if not audio:
            report.errors.append('O proxy não possui faixa de áudio.')
            return report
        video_width = int(video.get('width') or 0)
        video_height = int(video.get('height') or 0)
        report.metrics.update({'video_width': video_width, 'video_height': video_height})
        if expected_width and expected_height and (
            video_width != int(expected_width) or video_height != int(expected_height)
        ):
            report.errors.append('A geometria do proxy diverge do preview planejado.')
        video_start = self._stream_time_ms(video, 'start_time')
        audio_start = self._stream_time_ms(audio, 'start_time')
        video_duration = self._stream_time_ms(video, 'duration')
        audio_duration = self._stream_time_ms(audio, 'duration')
        report.metrics.update({
            'video_start_ms': video_start,
            'audio_start_ms': audio_start,
            'video_duration_ms': video_duration,
            'audio_duration_ms': audio_duration,
        })
        # A few AAC priming samples are normal. Anything past 120 ms is a
        # real editor-visible gap, not encoder padding.
        if abs(audio_start - video_start) > 120:
            report.errors.append('O áudio do proxy não inicia sincronizado com o vídeo.')
        if video_duration and audio_duration and abs(video_duration - audio_duration) > 350:
            report.errors.append('A duração do áudio do proxy diverge do vídeo.')
        if expected_duration_ms and duration_ms:
            drift = abs(duration_ms - int(expected_duration_ms))
            report.metrics['expected_duration_ms'] = int(expected_duration_ms)
            report.metrics['duration_drift_ms'] = drift
            if drift > max(350, round(int(expected_duration_ms) * .015)):
                report.errors.append('A duração do proxy diverge da fonte.')
        return report

    @staticmethod
    def validate_cuts(cuts, duration_ms):
        report = QualityReport(metrics={'cut_count': len(cuts)})
        previous_end = 0
        for cut in sorted(cuts, key=lambda item: (item.start_ms, item.end_ms)):
            if cut.start_ms < 0 or cut.end_ms <= cut.start_ms or cut.end_ms > duration_ms:
                report.errors.append('Há um intervalo de corte fora da duração do vídeo.')
                break
            if cut.start_ms < previous_end:
                report.errors.append('Há intervalos de corte sobrepostos.')
                break
            previous_end = cut.end_ms
        return report

    @staticmethod
    def validate_subtitles(tracks, protected_ranges):
        report = QualityReport()
        ranges = [
            (int(item.get('start_ms') or 0), int(item.get('end_ms') or 0))
            for item in (protected_ranges or [])
            if int(item.get('end_ms') or 0) > int(item.get('start_ms') or 0)
        ]
        overlaps = []
        for track in tracks:
            for cue in track.cues.all():
                if any(cue.start_ms < end and cue.end_ms > start for start, end in ranges):
                    overlaps.append({'language': track.language, 'cue': cue.cue_index})
        report.metrics['protected_subtitle_overlaps'] = overlaps
        if overlaps:
            report.errors.append('Há legendas sobre blocos que deveriam permanecer intactos.')
        return report

    def _probe(self, path):
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-show_streams', '-show_format',
            '-of', 'json', str(path),
        ])
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise ExternalMediaError('O arquivo final não pôde ser validado pelo FFprobe.') from exc

    @staticmethod
    def _duration_ms(probe):
        try:
            return max(0, round(float((probe.get('format') or {}).get('duration')) * 1000))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _stream_time_ms(stream, key):
        try:
            return round(float(stream.get(key) or 0) * 1000)
        except (TypeError, ValueError):
            return 0

    def _long_silences(self, path):
        result = self.runner.run_capture([
            settings.FFMPEG_BINARY, '-hide_banner', '-i', str(path),
            '-vn', '-af', 'silencedetect=noise=-45dB:d=8', '-f', 'null', '-',
        ])
        starts = [float(value) for value in re.findall(r'silence_start:\s*([0-9.]+)', result.stderr)]
        ends = [float(value) for value in re.findall(r'silence_end:\s*([0-9.]+)', result.stderr)]
        return [
            {'start_ms': round(start * 1000), 'end_ms': round(end * 1000)}
            for start, end in zip(starts, ends)
        ]
