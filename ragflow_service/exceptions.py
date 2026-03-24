class ServiceError(Exception):
    """Base error for the service."""


class ConfigError(ServiceError):
    """Raised when the service configuration is invalid."""


class ValidationError(ServiceError):
    """Raised when the incoming request is invalid."""


class RagflowAPIError(ServiceError):
    """Raised when RAGFlow returns a non-success response."""

    def __init__(self, message, status_code=502, code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}


class LLMAPIError(ServiceError):
    """Raised when the upstream LLM returns a non-success response."""

    def __init__(self, message, status_code=502, code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}


class KnowledgePortalAPIError(ServiceError):
    """Raised when the upstream knowledge portal returns a non-success response."""

    def __init__(self, message, status_code=502, code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.payload = payload or {}
