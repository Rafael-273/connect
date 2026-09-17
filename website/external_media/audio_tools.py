"""Capacidades declarativas de áudio para o futuro planner inteligente."""

from .audio_analysis import AudioAnalysisService
from .audio_mastering import AudioMasteringService
from .audio_mixing import AudioMixingService
from .audio_muxing import AudioMuxingService
from .audio_noise import AudioCleanupService, AudioNoiseAnalysisService
from .dialogue_processing import DialogueProcessingService


AUDIO_TOOL_REGISTRY = {
    'AnalyzeAudioTool': AudioAnalysisService,
    'AudioNoiseAnalysisTool': AudioNoiseAnalysisService,
    'AudioCleanupTool': AudioCleanupService,
    'DialogueProcessingTool': DialogueProcessingService,
    'AudioMixingTool': AudioMixingService,
    'AudioMasteringTool': AudioMasteringService,
    'AudioMuxingTool': AudioMuxingService,
}
