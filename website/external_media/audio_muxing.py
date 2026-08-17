from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioMuxResult:
    path: Path
    video_reencoded: bool


class AudioMuxingService:
    """Substitui o áudio preservando o stream visual; reencode é apenas fallback."""

    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def mux(self, video_path: Path, audio_path: Path, output_path: Path) -> AudioMuxResult:
        common = [
            settings.FFMPEG_BINARY, '-y', '-i', str(video_path), '-i', str(audio_path),
            '-map', '0:v:0', '-map', '1:a:0', '-c:a', 'aac', '-b:a', '256k',
            '-shortest', '-movflags', '+faststart',
        ]
        try:
            self.runner.run([*common, '-c:v', 'copy', str(output_path)])
            return AudioMuxResult(output_path, False)
        except ExternalMediaError:
            logger.warning('Remux sem reencode incompatível; usando fallback visual mínimo.', exc_info=True)
            self.runner.run([
                *common, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18',
                '-pix_fmt', 'yuv420p', str(output_path),
            ])
            return AudioMuxResult(output_path, True)

