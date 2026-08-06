"""Startup-time safety checks.

These guards fail fast (``sys.exit(1)``) when the process is started in a
non-development environment with obviously-insecure defaults. It is
preferable to crash-loop on deploy than to silently run with the well-known
default JWT secret.
"""

from __future__ import annotations

import sys

import structlog

from ai_platform.config import AppSettings

logger = structlog.get_logger()

# Default values considered unsafe for non-development environments.
_UNSAFE_DEFAULTS: dict[str, str] = {
    "jwt_secret_key": "change-me-in-production",
    "app_secret_key": "change-me-in-production-use-a-random-64-char-string",
}


def validate_secrets(settings: AppSettings) -> None:
    """Exit the process if a well-known default secret is used in staging/production.

    This runs early in the application lifespan so the pod fails to start
    rather than silently accepting JWTs signed with a public secret.
    """
    if settings.app_env == "development":
        return

    for field_name, default_value in _UNSAFE_DEFAULTS.items():
        actual = getattr(settings, field_name, None)
        if actual == default_value:
            logger.error(
                "FATAL: insecure default secret in non-development environment",
                field=field_name,
                environment=settings.app_env,
                hint=f"Set {field_name.upper()} to a random value before deploying.",
            )
            sys.exit(1)


__all__ = ["validate_secrets"]
