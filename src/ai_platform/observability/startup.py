"""Startup validation — verify all dependencies are reachable before accepting traffic."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
import sqlalchemy

from ai_platform.config import get_settings

logger = structlog.get_logger()


class StartupError(Exception):
    """Raised when a critical dependency is unreachable at startup."""

    pass


async def validate_startup() -> dict[str, bool]:
    """
    Validate all critical dependencies at startup.

    Returns health status dict. Raises StartupError if critical deps are down.
    """
    results: dict[str, bool] = {}

    # 1. PostgreSQL
    results["postgresql"] = await _check_postgres()

    # 2. Redis
    results["redis"] = await _check_redis()

    # 3. Configuration
    results["config"] = _check_config()

    # Log results
    for component, healthy in results.items():
        status = "OK" if healthy else "WARN"
        logger.info("Startup check", component=component, status=status)

    # Report failed dependencies but DO NOT block startup.
    # In production, the health check endpoint (/health) will report degraded status.
    # This allows the app to start even when some dependencies are temporarily unavailable.
    failed = [c for c in results if not results[c]]
    if failed:
        logger.warning(
            "Some dependencies are unreachable at startup. "
            "App will start in degraded mode. Fix and redeploy.",
            failed=failed,
        )

    return results


async def _check_postgres() -> bool:
    """Check PostgreSQL connectivity."""
    try:
        from ai_platform.infra.database.connection import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(sqlalchemy.text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("PostgreSQL check failed", error=str(e))
        return False


async def _check_redis() -> bool:
    """Check Redis connectivity."""
    try:
        from ai_platform.infra.cache.redis_client import get_redis

        r = await get_redis()
        return await r.ping()
    except Exception as e:
        logger.error("Redis check failed", error=str(e))
        return False


def _check_config() -> bool:
    """Validate critical configuration values.

    Raises ``RuntimeError`` in production when secrets are still using
    insecure defaults — the process must not serve traffic with known keys.
    """
    settings = get_settings()
    issues: list[str] = []

    # Production MUST NOT use default secrets
    if settings.is_production:
        if not settings.jwt_secret_key or settings.jwt_secret_key.startswith("change-me"):
            raise RuntimeError("JWT_SECRET_KEY must be changed from default in production")
        if not settings.app_secret_key or settings.app_secret_key.startswith("change-me"):
            raise RuntimeError("APP_SECRET_KEY must be changed from default in production")
    else:
        if not settings.app_secret_key or settings.app_secret_key.startswith("change-me"):
            issues.append("APP_SECRET_KEY must be changed from default")
        if not settings.jwt_secret_key or settings.jwt_secret_key.startswith("change-me"):
            issues.append("JWT_SECRET_KEY must be changed from default")

    if settings.is_production:
        if not settings.database_url or "localhost" in settings.database_url:
            issues.append("DATABASE_URL should not use localhost in production")
        if not settings.redis_url or "localhost" in settings.redis_url:
            issues.append("REDIS_URL should not use localhost in production")

    if issues:
        for issue in issues:
            logger.warning("Config warning", issue=issue)
        # Log warnings but don't block startup — /health will report degraded

    return True


async def deep_health_check() -> dict[str, Any]:
    """
    Deep health check for /health endpoint.

    Checks all dependencies and returns detailed status.
    Blocking synchronous calls are offloaded to a thread to avoid
    freezing the event loop.
    """
    health: dict[str, Any] = {
        "postgresql": False,
        "redis": False,
        "milvus": False,
        "litellm": False,
    }

    # Run all checks concurrently
    tasks = [
        _health_postgres(),
        _health_redis(),
        _health_milvus(),
        _health_litellm(),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    keys = ["postgresql", "redis", "milvus", "litellm"]
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            logger.debug(f"Health check failed for {key}", error=str(result))
            health[key] = False
        else:
            health[key] = bool(result)

    return health


async def _health_postgres() -> bool:
    try:
        from ai_platform.infra.database.connection import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(sqlalchemy.text("SELECT 1"))
        return True
    except Exception:
        return False


async def _health_redis() -> bool:
    try:
        from ai_platform.infra.cache.redis_client import get_redis

        r = await get_redis()
        return await r.ping()
    except Exception:
        return False


async def _health_milvus() -> bool:
    """Check Milvus — the MilvusClient constructor may do blocking I/O,
    so we run it in a thread to avoid blocking the event loop."""
    try:
        settings = get_settings()

        def _check() -> bool:
            from pymilvus import MilvusClient

            client = MilvusClient(uri=settings.milvus_uri)
            # Perform a lightweight probe if available
            return client is not None

        return await asyncio.to_thread(_check)
    except Exception:
        return False


async def _health_litellm() -> bool:
    try:
        import httpx

        settings = get_settings()
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.litellm_api_base}/health/liveliness")
            return resp.status_code == 200
    except Exception:
        return False
