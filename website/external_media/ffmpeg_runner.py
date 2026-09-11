from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings

from .exceptions import ExternalMediaError
from .media_input import RemoteMediaSource

logger = logging.getLogger(__name__)


class FFmpegRunner:
    @staticmethod
    def input_arg(value):
        """Keep remote-input metadata while preserving legacy local command strings."""
        return value if isinstance(value, RemoteMediaSource) else str(value)

    def run(self, command: list[str]) -> str:
        return self.run_capture(command).stdout

    def run_capture(self, command: list[str]) -> subprocess.CompletedProcess:
        """Runs ffmpeg/ffprobe and returns the full completed process (stdout + stderr).

        Analysis-only filters (loudnorm, volumedetect, ebur128, ...) print their
        measurements to stderr even on success, so callers that need those values
        should use this instead of `run`.
        """
        started = time.perf_counter()
        remote_inputs = [
            item for item in command
            if isinstance(item, RemoteMediaSource) or self._is_signed_remote_url(item)
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=settings.EXTERNAL_MEDIA_FFMPEG_TIMEOUT,
            )
            self._log_io_metrics(command, remote_inputs, started)
            return result
        except FileNotFoundError as exc:
            raise ExternalMediaError('FFmpeg/FFprobe não está instalado no worker.') from exc
        except subprocess.TimeoutExpired:
            # TimeoutExpired includes the full command in its repr. Do not chain
            # it because the command may contain a short-lived signed URL.
            raise ExternalMediaError('O processamento de vídeo excedeu o tempo limite.') from None
        except subprocess.CalledProcessError as exc:
            logger.error('FFmpeg falhou: %s', self.redact_sensitive_urls((exc.stderr or '')[-4000:]))
            # CalledProcessError also embeds the command; keeping it as __cause__
            # would leak the presigned query in an outer exception traceback.
            raise ExternalMediaError('O FFmpeg não conseguiu processar este vídeo.') from None

    @staticmethod
    def redact_sensitive_urls(value):
        """Remove signed query strings before subprocess diagnostics reach logs."""
        def redact(match):
            raw = match.group(0)
            parsed = urlsplit(raw)
            query = parsed.query.lower()
            if not any(token in query for token in ('x-amz-', 'signature=', 'awsaccesskeyid=')):
                return raw
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '[REDACTED]', ''))

        return re.sub(r'https?://[^\s\'\"]+', redact, str(value or ''))

    @staticmethod
    def _log_io_metrics(command, remote_inputs, started):
        elapsed = time.perf_counter() - started
        output_bytes = 0
        if command:
            candidate = Path(str(command[-1]))
            try:
                if candidate.is_file():
                    output_bytes = candidate.stat().st_size
            except OSError:
                pass
        logger.info(
            'ffmpeg_io input_mode=%s remote_inputs=%s bytes_read_from_s3_estimate=%s '
            'remote_read_duration_seconds=%.3f remote_seek_count=%s output_bytes=%s '
            'local_temp_peak_usage=%s processing_duration_seconds=%.3f',
            'S3_STREAM' if remote_inputs else 'LOCAL',
            len(remote_inputs),
            sum(getattr(item, 'size_bytes', 0) for item in remote_inputs),
            elapsed if remote_inputs else 0,
            sum(1 for item in command if str(item) == '-ss') if remote_inputs else 0,
            output_bytes,
            FFmpegRunner._workspace_usage(),
            elapsed,
        )

    @staticmethod
    def _is_signed_remote_url(value):
        raw = str(value)
        if not raw.startswith(('http://', 'https://')):
            return False
        query = urlsplit(raw).query.lower()
        return any(token in query for token in ('x-amz-', 'signature=', 'awsaccesskeyid='))

    @staticmethod
    def _workspace_usage():
        root = Path(settings.EXTERNAL_MEDIA_WORKSPACE_ROOT)
        if not root.exists():
            return 0
        try:
            return sum(item.stat().st_size for item in root.rglob('*') if item.is_file())
        except OSError:
            return 0
