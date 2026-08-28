from __future__ import annotations

import logging
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


class DiarizationUnavailable(RuntimeError):
    """Raised when Community-1 cannot be used in the current worker."""


@dataclass(frozen=True)
class SpeakerTurn:
    start_ms: int
    end_ms: int
    speaker: str

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms


class CommunitySpeakerDiarizer:
    """Lazy, process-wide loader for pyannote Community-1."""

    _pipeline = None
    _pipeline_key = None
    _lock = threading.Lock()

    def __init__(self, model=None, token=None, device=None):
        self.model = model or settings.EXTERNAL_MEDIA_DIARIZATION_MODEL
        self.token = token if token is not None else settings.HUGGINGFACE_TOKEN
        self.device = device or settings.EXTERNAL_MEDIA_DIARIZATION_DEVICE

    @property
    def enabled(self):
        return bool(settings.EXTERNAL_MEDIA_DIARIZATION_ENABLED)

    def diarize(self, audio_path: Path) -> list[SpeakerTurn]:
        if not self.enabled:
            raise DiarizationUnavailable('A diarização está desativada neste worker.')
        if not self.token and not Path(self.model).exists():
            raise DiarizationUnavailable(
                'Defina HUGGINGFACE_TOKEN e aceite os termos do modelo Community-1.'
            )
        pipeline = self._load_pipeline()
        try:
            output = pipeline(self._waveform_input(audio_path), min_speakers=2, max_speakers=2)
        except Exception as exc:
            raise DiarizationUnavailable(f'Community-1 não conseguiu analisar o áudio: {exc}') from exc
        annotation = getattr(output, 'exclusive_speaker_diarization', None)
        if annotation is None:
            annotation = getattr(output, 'speaker_diarization', None)
        if annotation is None:
            annotation = output
        turns = self._turns(annotation)
        if not turns:
            raise DiarizationUnavailable('Community-1 não retornou segmentos de locutor.')
        return turns

    @staticmethod
    def _waveform_input(audio_path: Path):
        """Avoid torchcodec: analysis audio is already 16 kHz mono PCM WAV."""
        try:
            import torch
            with wave.open(str(audio_path), 'rb') as stream:
                channels = stream.getnchannels()
                sample_width = stream.getsampwidth()
                sample_rate = stream.getframerate()
                raw = stream.readframes(stream.getnframes())
            if sample_width != 2:
                raise ValueError('áudio precisa estar em PCM de 16 bits')
            waveform = torch.frombuffer(bytearray(raw), dtype=torch.int16).to(torch.float32)
            waveform = waveform.reshape(-1, channels).transpose(0, 1) / 32768.0
            if channels > 1:
                waveform = waveform.mean(dim=0, keepdim=True)
            return {'waveform': waveform, 'sample_rate': sample_rate}
        except Exception as exc:
            raise DiarizationUnavailable(f'Não foi possível preparar o áudio para o Community-1: {exc}') from exc

    def _load_pipeline(self):
        key = (self.model, self.token, self.device)
        with self._lock:
            if self.__class__._pipeline is not None and self.__class__._pipeline_key == key:
                return self.__class__._pipeline
            try:
                import torch
                from pyannote.audio import Pipeline
            except ImportError as exc:
                raise DiarizationUnavailable(
                    'Instale requirements-diarization.txt no worker para usar o Community-1.'
                ) from exc
            try:
                pipeline = Pipeline.from_pretrained(self.model, token=self.token or None)
                if pipeline is None:
                    raise RuntimeError('o modelo não pôde ser carregado')
                device = self.device
                if device == 'auto':
                    device = 'cuda' if torch.cuda.is_available() else 'cpu'
                pipeline.to(torch.device(device))
            except Exception as exc:
                raise DiarizationUnavailable(f'Não foi possível carregar o Community-1: {exc}') from exc
            self.__class__._pipeline = pipeline
            self.__class__._pipeline_key = key
            logger.info('Community-1 carregado no dispositivo %s.', device)
            return pipeline

    @staticmethod
    def _turns(annotation):
        if hasattr(annotation, 'itertracks'):
            iterable = (
                (segment, speaker)
                for segment, _track, speaker in annotation.itertracks(yield_label=True)
            )
        else:
            iterable = iter(annotation)
        result = []
        for item in iterable:
            try:
                segment, speaker = item
                start_ms = max(0, round(float(segment.start) * 1000))
                end_ms = max(start_ms + 1, round(float(segment.end) * 1000))
            except (TypeError, ValueError, AttributeError):
                continue
            result.append(SpeakerTurn(start_ms, end_ms, str(speaker)))
        return sorted(result, key=lambda item: (item.start_ms, item.end_ms))
