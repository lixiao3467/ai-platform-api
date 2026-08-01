"""Startup validation — verify all dependencies are reachable before accepting traffic."""

from __future__ import annotations

import structlog

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
        status = "✓" if healthy else "✗"
        logger.info(f"Startup check: {status} {component}")

    # Critical dependencies that MUST be healthy
    critical = ["postgresql", "redis", "config"]
    failed = [c for c in critical if not results.get(c, False)]

    if failed:
        raise StartupError(
            f"Startup validation failed. "
            f"Critical dependencies unreachable: {', '.join(failed)}. "
            f"Refusing to start — fix dependencies and retry."
        )

    return results


async def _check_postgres() -> bool:
    """Check PostgreSQL connectivity."""
    try:
        from ai_platform.infra.database.connection import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
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
    """Validate critical configuration values."""
    settings = get_settings()
    issues = []

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
            logger.warning(f"Config warning: {issue}")
        # In production, config issues are fatal
        if settings.is_production:
            return False

    return True


async def deep_health_check() -> dict:
    """
    Deep health check for /health endpoint.

    Checks all dependencies and returns detailed status.
    """
    health = {
        "postgresql": False,
        "redis": False,
        "milvus": False,
        "litellm": False,
    }

    # PostgreSQL
    try:
        from ai_platform.infra.database.connection import get_engine

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        health["postgresql"] = True
    except Exception:
        pass

    # Redis
    try:
        from ai_platform.infra.cache.redis_client import get_redis

        r = await get_redis()
        health["redis"] = await r.ping()
    except Exception:
        pass

    # Milvus
    try:
        from pymilvus import MilvusClient
        from ai_platform.config import get_settings as gs

        settings = gs()
        client = MilvusClient(uri=settings.milvus_uri)
        health["milvus"] = True
    except Exception:
        pass

    # LiteLLM proxy
    try:
        import httpx

        settings = get_settings()
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.litellm_api_base}/health/liveliness")
            health["litellm"] = resp.status_code == 200
    except Exception:
        pass

    return health
