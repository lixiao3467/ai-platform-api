"""Tests for startup-time safety checks (Phase 0 — Task 2)."""

import pytest

from ai_platform.api.startup import validate_secrets
from ai_platform.config import AppSettings


def test_development_allows_default_secrets() -> None:
    """In development the well-known defaults must NOT cause an exit."""
    settings = AppSettings(
        app_env="development",
        jwt_secret_key="change-me-in-production",
        app_secret_key="change-me-in-production-use-a-random-64-char-string",
    )
    # Should return silently (no SystemExit)
    validate_secrets(settings)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_production_block_default_jwt_secret(env: str) -> None:
    settings = AppSettings(
        app_env=env,
        jwt_secret_key="change-me-in-production",
        app_secret_key="a-real-secret-value",
    )
    with pytest.raises(SystemExit):
        validate_secrets(settings)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_production_block_default_app_secret(env: str) -> None:
    settings = AppSettings(
        app_env=env,
        jwt_secret_key="a-real-secret-value",
        app_secret_key="change-me-in-production-use-a-random-64-char-string",
    )
    with pytest.raises(SystemExit):
        validate_secrets(settings)


@pytest.mark.parametrize("env", ["staging", "production"])
def test_staging_production_allows_overridden_secrets(env: str) -> None:
    settings = AppSettings(
        app_env=env,
        jwt_secret_key="super-secret-jwt-key-1234",
        app_secret_key="super-secret-app-key-5678",
    )
    validate_secrets(settings)
