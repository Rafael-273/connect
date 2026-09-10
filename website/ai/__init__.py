"""Shared artificial-intelligence services for the Connect platform."""

from .exceptions import AIConfigurationError, AIServiceError
from .service import AIService, TranscriptionSegment, get_ai_service

__all__ = [
    "AIConfigurationError",
    "AIService",
    "AIServiceError",
    "get_ai_service",
    "TranscriptionSegment",
]
