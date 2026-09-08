"""Detecção e redução conservadora de ruído — separando análise de execução."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from django.conf import settings

from .audio_utils import clamp, settings_from_config
from .ffmpeg_runner import FFmpegRunner
from .speech_edit import AudioActivity, SpeechEditService

logger = logging.getLogger(__name__)

CROSSFADE_MS = 120
LOCAL_MARGIN_MS = 180
MIN_EVENT_MS = 350
GLOBAL_MIN_CONTINUOUS_MS = 4000
FRAME_MS = 30


class NoiseType:
    CONTINUOUS_NOISE = 'CONTINUOUS_NOISE'
    TRANSIENT_NOISE = 'TRANSIENT_NOISE'
    ENVIRONMENTAL_NOISE = 'ENVIRONMENTAL_NOISE'
    ELECTRICAL_HUM = 'ELECTRICAL_HUM'
    UNKNOWN_NOISE = 'UNKNOWN_NOISE'


class RecommendedAction:
    APPLY = 'APPLY'
    REVIEW = 'REVIEW'
    KEEP_ORIGINAL = 'KEEP_ORIGINAL'


class ReductionMode:
    LOCAL = 'LOCAL'
    GLOBAL = 'GLOBAL'


class ReductionStrength:
    OFF = 'OFF'
    LIGHT = 'LIGHT'
    BALANCED = 'BALANCED'


NOISE_LABELS = {
    NoiseType.CONTINUOUS_NOISE: 'Ruído constante de fundo',
    NoiseType.TRANSIENT_NOISE: 'Ruído pontual',
    NoiseType.ENVIRONMENTAL_NOISE: 'Ruído ambiental',
    NoiseType.ELECTRICAL_HUM: 'Hum elétrico',
    NoiseType.UNKNOWN_NOISE: 'Ruído detectado',
}


@dataclass(frozen=True)
class NoiseCleanupSettings:
    """Configuração genérica de cleanup — reutilizável por qualquer template."""

    enabled: bool = True
    global_mode: str = ReductionStrength.OFF
    detect_transient_noise: bool = True
    transient_action: str = RecommendedAction.REVIEW
    auto_apply_continuous: bool = True
    confidence_auto_apply: float = 0.88

    @classmethod
    def from_config(cls, config):
        return settings_from_config(cls, config or {})


@dataclass(frozen=True)
class NoiseEvent:
    type: str
    start_ms: int
    end_ms: int
    confidence: float
    speech_overlap: bool
    recommended_action: str
    label: str = ''

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms

    def as_dict(self):
        return {
            'type': self.type,
            'start_ms': self.start_ms,
            'end_ms': self.end_ms,
            'confidence': round(self.confidence, 3),
            'speech_overlap': self.speech_overlap,
            'recommended_action': self.recommended_action,
            'label': self.label or NOISE_LABELS.get(self.type, 'Ruído detectado'),
        }


@dataclass(frozen=True)
class NoiseAnalysisPlan:
    events: tuple[NoiseEvent, ...]
    duration_ms: int
    global_noise_floor_db: float | None = None
    has_continuous_noise: bool = False

    def as_dict(self):
        return {
            'duration_ms': self.duration_ms,
            'global_noise_floor_db': self.global_noise_floor_db,
            'has_continuous_noise': self.has_continuous_noise,
            'events': [event.as_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, payload):
        if not payload:
            return cls((), 0)
        events = tuple(
            NoiseEvent(
                item.get('type', NoiseType.UNKNOWN_NOISE),
                int(item.get('start_ms') or 0),
                int(item.get('end_ms') or 0),
                float(item.get('confidence') or 0),
                bool(item.get('speech_overlap')),
                item.get('recommended_action', RecommendedAction.REVIEW),
                item.get('label') or '',
            )
            for item in (payload.get('events') or [])
        )
        return cls(
            events,
            int(payload.get('duration_ms') or 0),
            payload.get('global_noise_floor_db'),
            bool(payload.get('has_continuous_noise')),
        )


@dataclass(frozen=True)
class NoiseReductionDecision:
    start_ms: int
    end_ms: int
    mode: str
    strength: str
    source: str
    noise_type: str
    enabled: bool
    recommended_action: str
    speech_overlap: bool
    confidence: float
    label: str = ''
    event_index: int | None = None

    def as_dict(self):
        return {
            'start_ms': self.start_ms,
            'end_ms': self.end_ms,
            'mode': self.mode,
            'strength': self.strength,
            'source': self.source,
            'noise_type': self.noise_type,
            'enabled': self.enabled,
            'recommended_action': self.recommended_action,
            'speech_overlap': self.speech_overlap,
            'confidence': round(self.confidence, 3),
            'label': self.label,
            'event_index': self.event_index,
        }

    @classmethod
    def from_dict(cls, payload):
        return cls(
            int(payload.get('start_ms') or 0),
            int(payload.get('end_ms') or 0),
            payload.get('mode', ReductionMode.LOCAL),
            payload.get('strength', ReductionStrength.LIGHT),
            payload.get('source', 'AUTO_NOISE_ANALYSIS'),
            payload.get('noise_type', NoiseType.UNKNOWN_NOISE),
            bool(payload.get('enabled', False)),
            payload.get('recommended_action', RecommendedAction.REVIEW),
            bool(payload.get('speech_overlap')),
            float(payload.get('confidence') or 0),
            payload.get('label') or '',
            payload.get('event_index'),
        )


@dataclass
class NoiseCleanupResult:
    path: Path
    metrics: dict = field(default_factory=dict)


class NoiseReductionProvider(Protocol):
    """Abstração do algoritmo de redução — permite trocar implementação futuramente."""

    def reduce(
        self,
        input_path: Path,
        output_path: Path,
        *,
        strength: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> dict: ...


class FFmpegAfftdnProvider:
    """Implementação conservadora via FFmpeg `afftdn`."""

    STRENGTH_NR = {
        ReductionStrength.LIGHT: 8,
        ReductionStrength.BALANCED: 12,
    }

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def reduce(
        self,
        input_path: Path,
        output_path: Path,
        *,
        strength: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ):
        nr = self.STRENGTH_NR.get(strength, self.STRENGTH_NR[ReductionStrength.LIGHT])
        audio_filter = f'afftdn=nf=-25:nr={nr}:nt=w'
        if start_ms is not None and end_ms is not None and end_ms > start_ms:
            start_s = max(0, start_ms / 1000)
            end_s = end_ms / 1000
            audio_filter = (
                f'aselect=between(t\\,{start_s}\\,{end_s}),asetpts=PTS-STARTPTS,{audio_filter}'
            )
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(input_path),
            '-af', audio_filter,
            '-c:a', 'pcm_s16le', str(output_path),
        ])
        return {'provider': 'ffmpeg_afftdn', 'strength': strength, 'nr': nr}


class NoiseReductionDecisionBuilder:
    """Converte eventos detectados em decisões executáveis — sem lógica de FFmpeg."""

    @classmethod
    def build(cls, plan: NoiseAnalysisPlan, settings_: NoiseCleanupSettings):
        if not settings_.enabled:
            return []
        decisions = []
        if plan.has_continuous_noise and settings_.global_mode != ReductionStrength.OFF:
            decisions.append(NoiseReductionDecision(
                0,
                plan.duration_ms,
                ReductionMode.GLOBAL,
                settings_.global_mode,
                'AUTO_NOISE_ANALYSIS',
                NoiseType.CONTINUOUS_NOISE,
                settings_.auto_apply_continuous,
                RecommendedAction.APPLY if settings_.auto_apply_continuous else RecommendedAction.REVIEW,
                False,
                0.9,
                'Ruído constante de fundo',
            ))
        for index, event in enumerate(plan.events):
            if event.recommended_action == RecommendedAction.KEEP_ORIGINAL:
                continue
            strength = ReductionStrength.LIGHT
            if event.type == NoiseType.CONTINUOUS_NOISE and settings_.global_mode != ReductionStrength.OFF:
                strength = settings_.global_mode
            enabled = event.recommended_action == RecommendedAction.APPLY
            if (
                event.type in {NoiseType.TRANSIENT_NOISE, NoiseType.ENVIRONMENTAL_NOISE}
                and not settings_.detect_transient_noise
            ):
                continue
            if event.recommended_action == RecommendedAction.REVIEW:
                if settings_.transient_action == RecommendedAction.KEEP_ORIGINAL:
                    continue
                enabled = settings_.transient_action == RecommendedAction.APPLY
            decisions.append(NoiseReductionDecision(
                event.start_ms,
                event.end_ms,
                ReductionMode.LOCAL,
                strength,
                'AUTO_NOISE_ANALYSIS',
                event.type,
                enabled,
                event.recommended_action,
                event.speech_overlap,
                event.confidence,
                event.label,
                index,
            ))
        return decisions


class AudioNoiseAnalysisService:
    """Identifica ruídos e classifica risco para a voz — não altera áudio."""

    transient_minimum_ms = 250
    transient_maximum_ms = 6500
    spike_db = 8.0
    hum_variance_db = 2.5

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()
        self.editor = SpeechEditService(self.runner)

    def analyze(
        self,
        media_path: Path,
        *,
        speech_blocks=None,
        settings_: NoiseCleanupSettings | None = None,
    ) -> NoiseAnalysisPlan:
        settings_ = settings_ or NoiseCleanupSettings()
        duration_ms = self.editor.duration_ms(media_path)
        if not settings_.enabled or duration_ms <= 0:
            return NoiseAnalysisPlan((), duration_ms)
        wav_path = media_path.with_suffix('.noise-analysis.wav')
        self.editor.extract_analysis_audio(media_path, wav_path)
        try:
            activity = AudioActivity(wav_path, frame_ms=FRAME_MS)
            blocks = [
                (max(0, int(item.start_ms)), max(0, int(item.end_ms)))
                for item in (speech_blocks or [])
                if int(getattr(item, 'end_ms', 0) or 0) > int(getattr(item, 'start_ms', 0) or 0)
            ]
            if not blocks and isinstance(speech_blocks, list):
                blocks = [
                    (max(0, int(start)), max(0, int(end)))
                    for start, end in speech_blocks
                    if int(end) > int(start)
                ]
            events = []
            events.extend(self._detect_transients(activity, blocks, duration_ms, settings_))
            events.extend(self._detect_hum(activity, blocks, duration_ms))
            events.extend(self._detect_continuous(activity, blocks, duration_ms))
            events = self._merge_events(events, duration_ms)
            has_continuous = any(event.type == NoiseType.CONTINUOUS_NOISE for event in events)
            return NoiseAnalysisPlan(
                tuple(events),
                duration_ms,
                global_noise_floor_db=float(getattr(activity, 'voice_threshold_db', None) or 0) - 10,
                has_continuous_noise=has_continuous,
            )
        finally:
            wav_path.unlink(missing_ok=True)

    def _speech_overlap(self, start_ms, end_ms, blocks):
        return any(start_ms < block_end and end_ms > block_start for block_start, block_end in blocks)

    def _detect_transients(self, activity, blocks, duration_ms, settings_):
        if not settings_.detect_transient_noise:
            return []
        values = activity.db
        frame_ms = activity.frame_ms
        threshold = activity.voice_threshold_db + self.spike_db
        events = []
        index = 0
        while index < len(values):
            if values[index] < threshold:
                index += 1
                continue
            start_index = index
            peak = float(values[index])
            while index < len(values) and values[index] >= activity.voice_threshold_db - 2:
                peak = max(peak, float(values[index]))
                index += 1
            end_index = index
            start_ms = start_index * frame_ms
            end_ms = min(duration_ms, end_index * frame_ms)
            duration = end_ms - start_ms
            if duration < self.transient_minimum_ms or duration > self.transient_maximum_ms:
                continue
            overlap = self._speech_overlap(start_ms, end_ms, blocks)
            confidence = clamp(0.55 + (peak - threshold) / 20, 0.55, 0.97)
            if overlap:
                confidence = min(confidence, 0.82)
                action = RecommendedAction.REVIEW
                label = 'Possível ruído externo durante fala'
            else:
                action = RecommendedAction.APPLY if confidence >= settings_.confidence_auto_apply else RecommendedAction.REVIEW
                label = 'Ruído pontual detectado'
            events.append(NoiseEvent(
                NoiseType.TRANSIENT_NOISE if duration < 2200 else NoiseType.ENVIRONMENTAL_NOISE,
                start_ms,
                end_ms,
                confidence,
                overlap,
                action,
                label,
            ))
        return events

    def _detect_hum(self, activity, blocks, duration_ms):
        values = activity.db
        frame_ms = activity.frame_ms
        events = []
        index = 0
        while index < len(values):
            start_ms = index * frame_ms
            end_ms = min(duration_ms, start_ms + 2500)
            if self._speech_overlap(start_ms, end_ms, blocks):
                index += int(800 / frame_ms)
                continue
            slice_values = activity._slice(start_ms, end_ms)
            if slice_values.size < 20:
                index += 1
                continue
            spread = float(slice_values.max() - slice_values.min())
            median = float(slice_values.mean())
            if spread <= self.hum_variance_db and median > activity.voice_threshold_db - 18:
                confidence = clamp(0.7 + (self.hum_variance_db - spread) / 10, 0.7, 0.93)
                overlap = self._speech_overlap(start_ms, end_ms, blocks)
                events.append(NoiseEvent(
                    NoiseType.ELECTRICAL_HUM,
                    start_ms,
                    end_ms,
                    confidence,
                    overlap,
                    RecommendedAction.REVIEW if overlap else RecommendedAction.APPLY,
                    'Hum elétrico provável',
                ))
                index += int(end_ms / frame_ms)
                continue
            index += 1
        return events

    def _detect_continuous(self, activity, blocks, duration_ms):
        values = activity.db
        frame_ms = activity.frame_ms
        floor = float(getattr(activity, 'voice_threshold_db', -40)) - 14
        events = []
        index = 0
        while index < len(values):
            start_ms = index * frame_ms
            if self._speech_overlap(start_ms, start_ms + frame_ms, blocks):
                index += 1
                continue
            if values[index] < floor:
                index += 1
                continue
            start_index = index
            while index < len(values) and values[index] >= floor and not self._speech_overlap(
                index * frame_ms, (index + 1) * frame_ms, blocks,
            ):
                index += 1
            end_ms = min(duration_ms, index * frame_ms)
            start_ms = start_index * frame_ms
            if end_ms - start_ms < GLOBAL_MIN_CONTINUOUS_MS:
                continue
            confidence = clamp(0.75 + (end_ms - start_ms) / 120000, 0.75, 0.95)
            events.append(NoiseEvent(
                NoiseType.CONTINUOUS_NOISE,
                start_ms,
                end_ms,
                confidence,
                False,
                RecommendedAction.APPLY,
                'Ruído constante de fundo',
            ))
        return events

    @staticmethod
    def _merge_events(events, duration_ms):
        if not events:
            return []
        ordered = sorted(events, key=lambda item: (item.start_ms, item.end_ms))
        merged = [ordered[0]]
        for event in ordered[1:]:
            previous = merged[-1]
            if event.type == previous.type and event.start_ms <= previous.end_ms + 250:
                merged[-1] = NoiseEvent(
                    previous.type,
                    previous.start_ms,
                    max(previous.end_ms, event.end_ms),
                    max(previous.confidence, event.confidence),
                    previous.speech_overlap or event.speech_overlap,
                    previous.recommended_action if previous.recommended_action == RecommendedAction.REVIEW else event.recommended_action,
                    previous.label or event.label,
                )
            else:
                merged.append(event)
        return [event for event in merged if event.duration_ms >= MIN_EVENT_MS and event.end_ms <= duration_ms]


class AudioCleanupService:
    """Executa apenas decisões aprovadas — sem inteligência editorial."""

    def __init__(self, runner=None, provider: NoiseReductionProvider | None = None):
        self.runner = runner or FFmpegRunner()
        self.provider = provider or FFmpegAfftdnProvider(self.runner)
        self.editor = SpeechEditService(self.runner)

    def apply(
        self,
        media_path: Path,
        output_path: Path,
        decisions: list[NoiseReductionDecision],
        *,
        settings_: NoiseCleanupSettings | None = None,
    ) -> NoiseCleanupResult:
        settings_ = settings_ or NoiseCleanupSettings()
        active = [
            decision for decision in decisions
            if decision.enabled and decision.strength != ReductionStrength.OFF
        ]
        if not active:
            self._copy(media_path, output_path)
            return NoiseCleanupResult(output_path, {'applied': 0})
        global_decisions = [item for item in active if item.mode == ReductionMode.GLOBAL]
        local_decisions = [item for item in active if item.mode == ReductionMode.LOCAL]
        current = media_path
        metrics = {'applied': 0, 'global': 0, 'local': 0, 'segments': []}
        workdir = output_path.parent
        if global_decisions:
            strongest = max(
                global_decisions,
                key=lambda item: self.provider.STRENGTH_NR.get(item.strength, 0)
                if hasattr(self.provider, 'STRENGTH_NR')
                else 1,
            )
            global_path = workdir / 'noise_global.wav'
            extracted = workdir / 'noise_source.wav'
            self._extract_audio(current, extracted)
            self.provider.reduce(extracted, global_path, strength=strongest.strength)
            global_video = workdir / 'noise_global.mp4'
            self._mux_audio(current, global_path, global_video)
            current = global_video
            metrics['global'] += 1
            metrics['applied'] += 1
        for index, decision in enumerate(local_decisions):
            segment_start = max(0, decision.start_ms - LOCAL_MARGIN_MS)
            segment_end = decision.end_ms + LOCAL_MARGIN_MS
            treated = workdir / f'noise_local_{index}.mp4'
            self._apply_local(current, treated, decision, segment_start, segment_end)
            current = treated
            metrics['local'] += 1
            metrics['applied'] += 1
            metrics['segments'].append({
                'start_ms': decision.start_ms,
                'end_ms': decision.end_ms,
                'strength': decision.strength,
                'noise_type': decision.noise_type,
            })
        self._copy(current, output_path)
        return NoiseCleanupResult(output_path, metrics)

    def render_preview_clip(
        self,
        media_path: Path,
        output_path: Path,
        decision: NoiseReductionDecision,
        *,
        variant: str = 'treated',
    ):
        start_ms = max(0, decision.start_ms - LOCAL_MARGIN_MS)
        end_ms = decision.end_ms + LOCAL_MARGIN_MS
        if variant == 'original':
            self.runner.run([
                settings.FFMPEG_BINARY, '-y',
                '-ss', f'{start_ms / 1000:.3f}',
                '-i', str(media_path),
                '-t', f'{(end_ms - start_ms) / 1000:.3f}',
                '-vn', '-c:a', 'aac', '-b:a', '128k', str(output_path),
            ])
            return
        temp = output_path.with_suffix('.processed.wav')
        segment = output_path.with_suffix('.segment.wav')
        self.runner.run([
            settings.FFMPEG_BINARY, '-y',
            '-ss', f'{start_ms / 1000:.3f}',
            '-i', str(media_path),
            '-t', f'{(end_ms - start_ms) / 1000:.3f}',
            '-vn', '-c:a', 'pcm_s16le', str(segment),
        ])
        self.provider.reduce(segment, temp, strength=decision.strength)
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(temp),
            '-c:a', 'aac', '-b:a', '128k', str(output_path),
        ])
        segment.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)

    def _apply_local(self, media_path, output_path, decision, segment_start, segment_end):
        duration_ms = self.editor.duration_ms(media_path)
        segment_end = min(duration_ms, segment_end)
        start_s = segment_start / 1000
        end_s = segment_end / 1000
        duration_s = duration_ms / 1000
        nr = FFmpegAfftdnProvider.STRENGTH_NR.get(decision.strength, 8)
        # Crossfading overlaps samples and shortens the audio while the copied
        # video stream keeps its duration. Concatenating the treated slice keeps
        # the original timestamp and duration exactly intact.
        filter_complex = (
            f'[0:a]atrim=0:{start_s},asetpts=PTS-STARTPTS[head];'
            f'[0:a]atrim={start_s}:{end_s},asetpts=PTS-STARTPTS,afftdn=nf=-25:nr={nr}:nt=w[body];'
            f'[0:a]atrim={end_s}:{duration_s},asetpts=PTS-STARTPTS[tail];'
            '[head][body][tail]concat=n=3:v=0:a=1[aout]'
        )
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(media_path),
            '-filter_complex', filter_complex,
            '-map', '0:v:0?', '-map', '[aout]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            str(output_path),
        ])

    def _extract_audio(self, media_path, wav_path):
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(media_path),
            '-vn', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', str(wav_path),
        ])

    def _mux_audio(self, video_path, audio_path, output_path):
        self.runner.run([
            settings.FFMPEG_BINARY, '-y',
            '-i', str(video_path), '-i', str(audio_path),
            '-map', '0:v:0?', '-map', '1:a:0',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-shortest', str(output_path),
        ])

    def _copy(self, source, destination):
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(source), '-c', 'copy', str(destination),
        ])
