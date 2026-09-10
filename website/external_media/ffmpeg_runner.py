from __future__ import annotations

import logging
import subprocess

from django.conf import settings

from .exceptions import ExternalMediaError

logger = logging.getLogger(__name__)


class FFmpegRunner:
    def run(self, command: list[str]) -> str:
        return self.run_capture(command).stdout

    def run_capture(self, command: list[str]) -> subprocess.CompletedProcess:
        """Runs ffmpeg/ffprobe and returns the full completed process (stdout + stderr).

        Analysis-only filters (loudnorm, volumedetect, ebur128, ...) print their
        measurements to stderr even on success, so callers that need those values
        should use this instead of `run`.
        """
        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=settings.EXTERNAL_MEDIA_FFMPEG_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise ExternalMediaError('FFmpeg/FFprobe não está instalado no worker.') from exc
        except subprocess.TimeoutExpired as exc:
            raise ExternalMediaError('O processamento de vídeo excedeu o tempo limite.') from exc
        except subprocess.CalledProcessError as exc:
            logger.error('FFmpeg falhou: %s', exc.stderr[-4000:])
            raise ExternalMediaError('O FFmpeg não conseguiu processar este vídeo.') from exc
