class AIServiceError(RuntimeError):
    """Base error raised when an AI provider request fails."""


class AIConfigurationError(AIServiceError):
    """Raised when the AI integration is used without valid configuration."""
