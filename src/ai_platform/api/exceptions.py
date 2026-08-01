"""Custom exception hierarchy for AI Platform."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base application error."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    message: str = "An internal error occurred"

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: Any | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.detail = detail
        super().__init__(self.message)


# =============================================================================
# 4xx Client Errors
# =============================================================================


class BadRequestError(AppError):
    status_code = 400
    error_code = "BAD_REQUEST"
    message = "Invalid request"


class AuthenticationError(AppError):
    status_code = 401
    error_code = "UNAUTHORIZED"
    message = "Authentication required"


class ForbiddenError(AppError):
    status_code = 403
    error_code = "FORBIDDEN"
    message = "Permission denied"


class NotFoundError(AppError):
    status_code = 404
    error_code = "NOT_FOUND"
    message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    error_code = "CONFLICT"
    message = "Resource conflict"


class RateLimitError(AppError):
    status_code = 429
    error_code = "RATE_LIMITED"
    message = "Too many requests"


class ValidationError(AppError):
    status_code = 422
    error_code = "VALIDATION_ERROR"
    message = "Validation failed"


# =============================================================================
# AI-Specific Errors
# =============================================================================


class ModelUnavailableError(AppError):
    status_code = 503
    error_code = "MODEL_UNAVAILABLE"
    message = "Requested model is currently unavailable"


class ModelTimeoutError(AppError):
    status_code = 504
    error_code = "MODEL_TIMEOUT"
    message = "Model request timed out"


class TokenLimitExceededError(AppError):
    status_code = 400
    error_code = "TOKEN_LIMIT_EXCEEDED"
    message = "Input exceeds model's token limit"


class QuotaExceededError(AppError):
    status_code = 429
    error_code = "QUOTA_EXCEEDED"
    message = "Usage quota exceeded"


# =============================================================================
# Knowledge Base Errors
# =============================================================================


class DocumentParseError(AppError):
    status_code = 422
    error_code = "DOCUMENT_PARSE_ERROR"
    message = "Failed to parse document"


class IngestionError(AppError):
    status_code = 500
    error_code = "INGESTION_ERROR"
    message = "Document ingestion failed"


# =============================================================================
# Agent Errors
# =============================================================================


class AgentExecutionError(AppError):
    status_code = 500
    error_code = "AGENT_EXECUTION_ERROR"
    message = "Agent execution failed"


class ToolExecutionError(AppError):
    status_code = 500
    error_code = "TOOL_EXECUTION_ERROR"
    message = "Tool execution failed"
