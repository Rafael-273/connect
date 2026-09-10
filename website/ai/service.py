"""Reusable OpenAI access for text generation and audio transcription.

Keep provider-specific calls in this module. Views, consumers and background jobs
should depend on ``AIService`` instead of creating OpenAI clients themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from django.conf import settings

from .exceptions import AIConfigurationError, AIServiceError

logger = logging.getLogger(__name__)
WHISPER_TIMESTAMP_MODEL = 'whisper-1'


@dataclass(frozen=True)
class TranscriptionSegment:
    start_ms: int
    end_ms: int
    text: str
    granularity: str = 'segment'


class AIService:
    """Gateway for the AI capabilities used by Connect.

    The OpenAI client is created lazily, so importing Django modules does not fail
    in environments where AI is not configured. A client can also be injected to
    make application code straightforward to test.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        text_model: str | None = None,
        transcription_model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.OPENAI_API_KEY
        self.text_model = text_model
        self.transcription_model = (
            transcription_model or settings.OPENAI_TRANSCRIPTION_MODEL
        )
        self._client = client

    @property
    def client(self) -> Any:
        """Return the configured SDK client, creating it on first use."""
        if self._client is None:
            if not self.api_key:
                raise AIConfigurationError("OPENAI_API_KEY não está configurada.")

            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - deployment guard
                raise AIConfigurationError(
                    "O pacote 'openai' não está instalado."
                ) from exc

            self._client = OpenAI(api_key=self.api_key)

        return self._client

    def generate_text(
        self,
        prompt: str,
        *,
        instructions: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
        safety_identifier: str | None = None,
    ) -> str:
        """Generate text through the OpenAI Responses API.

        ``safety_identifier`` should be a stable, non-identifying user reference.
        """
        if not prompt or not prompt.strip():
            raise ValueError("prompt não pode ser vazio.")
        selected_model = model or self.text_model
        if not selected_model:
            raise AIConfigurationError(
                "Informe o modelo de texto na chamada ou ao criar o AIService."
            )

        request: dict[str, Any] = {
            "model": selected_model,
            "input": prompt,
        }
        if instructions:
            request["instructions"] = instructions
        if max_output_tokens is not None:
            request["max_output_tokens"] = max_output_tokens
        if safety_identifier:
            request["safety_identifier"] = safety_identifier

        try:
            response = self.client.responses.create(**request)
            text = (response.output_text or "").strip()
            if not text:
                raise AIServiceError("A OpenAI retornou uma resposta sem texto.")
            return text
        except AIServiceError:
            raise
        except Exception as exc:
            logger.exception("Falha ao gerar texto com o modelo %s", request["model"])
            raise AIServiceError("Não foi possível gerar a resposta de IA.") from exc

    def transcribe(
        self,
        audio: bytes | bytearray | BinaryIO | str | Path,
        *,
        filename: str = "audio.webm",
        content_type: str = "audio/webm",
        language: str = "pt",
        prompt: str | None = None,
        model: str | None = None,
    ) -> str:
        """Transcribe an uploaded file, filesystem path or in-memory audio bytes."""
        audio_file, should_close = self._prepare_audio(
            audio,
            filename=filename,
            content_type=content_type,
        )
        request: dict[str, Any] = {
            "model": model or self.transcription_model,
            "file": audio_file,
            "language": language,
        }
        if prompt:
            request["prompt"] = prompt

        try:
            response = self.client.audio.transcriptions.create(**request)
            text = (response.text or "").strip()
            if not text:
                raise AIServiceError("A OpenAI retornou uma transcrição vazia.")
            return text
        except AIServiceError:
            raise
        except Exception as exc:
            logger.exception(
                "Falha ao transcrever áudio com o modelo %s", request["model"]
            )
            raise AIServiceError("Não foi possível transcrever o áudio.") from exc
        finally:
            if should_close:
                audio_file.close()

    def transcribe_segments(
        self,
        audio: bytes | bytearray | BinaryIO | str | Path,
        *,
        filename: str = 'audio.mp3',
        content_type: str = 'audio/mpeg',
        language: str | None = 'pt',
        prompt: str | None = None,
        model: str | None = None,
    ) -> list[TranscriptionSegment]:
        """Transcribe audio with immutable segment timestamps (Whisper verbose JSON)."""
        audio_file, should_close = self._prepare_audio(
            audio, filename=filename, content_type=content_type,
        )
        selected_model = model or (
            WHISPER_TIMESTAMP_MODEL
            if self.transcription_model != WHISPER_TIMESTAMP_MODEL
            else self.transcription_model
        )
        request: dict[str, Any] = {
            'model': selected_model,
            'file': audio_file,
            'response_format': 'verbose_json',
            'timestamp_granularities': ['word', 'segment'],
        }
        if language:
            request['language'] = language
        if prompt:
            request['prompt'] = prompt

        try:
            response = self._transcribe_with_timestamps(request, selected_model)
            raw_words = getattr(response, 'words', None)
            if raw_words is None and isinstance(response, dict):
                raw_words = response.get('words')
            raw_segments = getattr(response, 'segments', None)
            if raw_segments is None and isinstance(response, dict):
                raw_segments = response.get('segments')
            segments = []
            raw_items = raw_words or raw_segments or []
            granularity = 'word' if raw_words else 'segment'
            for item in raw_items:
                value = item if isinstance(item, dict) else vars(item)
                text = str(value.get('word') or value.get('text', '')).strip()
                start_ms = max(0, round(float(value.get('start', 0)) * 1000))
                end_ms = max(start_ms + 1, round(float(value.get('end', 0)) * 1000))
                if text:
                    segments.append(TranscriptionSegment(start_ms, end_ms, text, granularity))
            if not segments:
                raise AIServiceError('A OpenAI retornou uma transcrição sem segmentos.')
            return segments
        except AIServiceError:
            raise
        except Exception as exc:
            logger.exception('Falha ao transcrever áudio com timestamps')
            raise AIServiceError('Não foi possível transcrever o áudio com timestamps.') from exc
        finally:
            if should_close:
                audio_file.close()

    def _transcribe_with_timestamps(
        self,
        request: dict[str, Any],
        selected_model: str,
    ) -> Any:
        try:
            return self.client.audio.transcriptions.create(**request)
        except Exception as exc:
            if (
                selected_model != WHISPER_TIMESTAMP_MODEL
                and self._error_indicates_timestamp_unsupported(exc)
            ):
                logger.warning(
                    'Modelo %s não suporta verbose_json/timestamps; usando %s para segmentos.',
                    selected_model,
                    WHISPER_TIMESTAMP_MODEL,
                )
                fallback_request = dict(request)
                fallback_request['model'] = WHISPER_TIMESTAMP_MODEL
                return self.client.audio.transcriptions.create(**fallback_request)
            raise

    @staticmethod
    def _error_indicates_timestamp_unsupported(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            'response_format' in message and 'not compatible' in message
        ) or 'timestamp_granularities' in message

    @staticmethod
    def _prepare_audio(
        audio: bytes | bytearray | BinaryIO | str | Path,
        *,
        filename: str,
        content_type: str,
    ) -> tuple[Any, bool]:
        if isinstance(audio, (bytes, bytearray)):
            stream = BytesIO(bytes(audio))
            stream.name = filename
            return stream, True

        if isinstance(audio, (str, Path)):
            return Path(audio).open("rb"), True

        if hasattr(audio, "read"):
            if not getattr(audio, "name", None):
                # File tuples preserve the format for unnamed in-memory streams.
                return (filename, audio.read(), content_type), False
            return audio, False

        raise TypeError("audio deve ser bytes, um arquivo ou um caminho válido.")


@lru_cache(maxsize=1)
def get_ai_service() -> AIService:
    """Return the process-wide AI gateway used by application modules."""
    return AIService()
