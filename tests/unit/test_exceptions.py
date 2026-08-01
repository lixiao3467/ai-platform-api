"""Tests for exception hierarchy."""

import pytest

from ai_platform.api.exceptions import (
    AgentExecutionError,
    AppError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    DocumentParseError,
    ForbiddenError,
    ModelTimeoutError,
    ModelUnavailableError,
    NotFoundError,
    QuotaExceededError,
    RateLimitError,
    TokenLimitExceededError,
    ToolExecutionError,
    ValidationError,
)


def test_base_app_error_defaults() -> None:
    err = AppError()
    assert err.status_code == 500
    assert err.error_code == "INTERNAL_ERROR"
    assert err.detail is None


def test_custom_message() -> None:
    err = NotFoundError("User not found")
    assert err.message == "User not found"
    assert err.status_code == 404
    assert err.error_code == "NOT_FOUND"


def test_detail_attachment() -> None:
    err = ValidationError("Invalid input", detail={"field": "email", "reason": "invalid format"})
    assert err.detail == {"field": "email", "reason": "invalid format"}


@pytest.mark.parametrize(
    "exc_class,expected_status",
    [
        (BadRequestError, 400),
        (AuthenticationError, 401),
        (ForbiddenError, 403),
        (NotFoundError, 404),
        (ConflictError, 409),
        (ValidationError, 422),
        (RateLimitError, 429),
        (QuotaExceededError, 429),
        (ModelUnavailableError, 503),
        (ModelTimeoutError, 504),
        (TokenLimitExceededError, 400),
        (DocumentParseError, 422),
        (AgentExecutionError, 500),
        (ToolExecutionError, 500),
    ],
)
def test_exception_status_codes(exc_class: type[AppError], expected_status: int) -> None:
    err = exc_class()
    assert err.status_code == expected_status


def test_all_exceptions_inherit_from_app_error() -> None:
    """All custom exceptions should inherit from AppError."""
    exceptions = [
        BadRequestError, AuthenticationError, ForbiddenError, NotFoundError,
        ConflictError, RateLimitError, ValidationError, ModelUnavailableError,
        ModelTimeoutError, TokenLimitExceededError, QuotaExceededError,
        DocumentParseError, AgentExecutionError, ToolExecutionError,
    ]
    for exc_class in exceptions:
        assert issubclass(exc_class, AppError)
        err = exc_class()
        assert isinstance(err, Exception)
