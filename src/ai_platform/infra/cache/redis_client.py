"""Redis client management."""

from __future__ import annotations

from urllib.parse import urlparse

import redis.asyncio as aioredis

from ai_platform.config import get_settings

_redis: aioredis.Redis | None = None


def _build_redis_kwargs(url: str) -> dict:
    """
    Build connection kwargs appropriate for the Redis URL.

    Upstash and other cloud providers use self-signed or non-standard
    TLS certificates. For `rediss://` (TLS) URLs, disable strict cert
    verification so connections succeed without sacrificing encryption.
    """
    kwargs: dict = {
        "decode_responses": True,
        "max_connections": 50,
    }
    parsed = urlparse(url)
    if parsed.scheme == "rediss":
        # Upstash, Railway Redis, etc. — TLS with self-signed certs
        kwargs["ssl_cert_reqs"] = "none"
    return kwargs


async def get_redis() -> aioredis.Redis:
    """Get or create the Redis connection singleton."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.redis_url,
            **_build_redis_kwargs(settings.redis_url),
        )
    return _redis


async def close_redis() -> None:
    """Close Redis connection (called on app shutdown)."""
    global _redis
    if _redis is not None:
        await _redis.close()
        _redis = None


async def redis_health() -> bool:
    """Check Redis connectivity."""
    try:
        r = await get_redis()
        return await r.ping()
    except Exception:
        return False
