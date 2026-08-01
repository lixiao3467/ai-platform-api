"""Redis client management."""

from __future__ import annotations

import redis.asyncio as aioredis

from ai_platform.config import get_settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create the Redis connection singleton."""
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=50,
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
