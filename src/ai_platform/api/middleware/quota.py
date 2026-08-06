"""Quota enforcement middleware.

On every request that touches a model (chat completions, agent runs,
workflow executions), the middleware checks whether the tenant's
real-time usage counter (stored in Redis) has exceeded the configured
limit.  If it has, the request is rejected with ``429 Too Many Requests``.

Usage as a FastAPI dependency::

    @router.post("/chat/completions", dependencies=[Depends(check_quota("model_calls"))])

The counter is persisted to PostgreSQL asynchronously (batched every
60 seconds by a background task — see ``services.quota_sync``).
"""

from __future__ import annotations

import json

import structlog
from fastapi import Depends, HTTPException, status

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.infra.cache.redis_client import get_redis

logger = structlog.get_logger()

# Redis key pattern: aip:quota:{tenant_id}:{resource_type}
_QUOTA_KEY_PREFIX = "aip:quota"

# Lua script: atomically INCRBY and (on first increment) EXPIRE.
# The original ``INCRBY`` + ``EXPIRE`` sequence was non-atomic: if the
# process crashed between the two commands the key would live forever
# (a silent quota leak). Lua ``EVAL`` is executed atomically by Redis.
_QUOTA_INCREMENT_LUA = """
local key = KEYS[1]
local increment = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])

local current = redis.call('INCRBY', key, increment)
if current == increment then
    redis.call('EXPIRE', key, ttl)
end
return current
"""

# 31 days — auto-reset monthly
_QUOTA_TTL_SECONDS = 31 * 86400


def _quota_key(tenant_id: str, resource_type: str) -> str:
    return f"{_QUOTA_KEY_PREFIX}:{tenant_id}:{resource_type}"


async def _get_tenant_quota_limit(tenant_id: str, resource_type: str) -> int | None:
    """Fetch the configured quota limit for a tenant+resource.

    Reads from the ``tenants.quota_config`` JSON column via Redis cache.
    Returns ``None`` if no limit is configured (unlimited).
    """
    redis = await get_redis()
    cache_key = f"aip:tenant_quota_config:{tenant_id}"

    cached = await redis.get(cache_key)
    if cached:
        quota_config = json.loads(cached)
    else:
        from sqlalchemy import select

        from ai_platform.domain.models import Tenant
        from ai_platform.infra.database.connection import get_session_factory

        import uuid

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(Tenant.quota_config).where(Tenant.id == uuid.UUID(tenant_id))
            )
            quota_config = result.scalar_one_or_none() or {}

        # Cache for 5 minutes
        await redis.setex(cache_key, 300, json.dumps(quota_config))

    # Map resource_type to the quota config key
    mapping = {
        "model_calls": "monthly_model_calls",
        "storage": "storage_bytes",
        "users": "max_users",
        "apps": "max_apps",
        "api_keys": "max_api_keys_per_app",
    }
    config_key = mapping.get(resource_type)
    if not config_key:
        return None

    limit = quota_config.get(config_key)
    if limit is None or limit <= 0:
        return None  # Unlimited
    return int(limit)


async def increment_quota(tenant_id: str, resource_type: str, amount: int = 1) -> int:
    """Atomically increment the real-time usage counter in Redis.

    Uses a Lua script so ``INCRBY`` + ``EXPIRE`` execute as a single atomic
    operation — the previous two-step version could leak immortal keys if
    the process crashed between the commands.

    Returns the new value.
    """
    redis = await get_redis()
    key = _quota_key(tenant_id, resource_type)
    new_value = await redis.eval(  # type: ignore[attr-defined]
        _QUOTA_INCREMENT_LUA,
        1,
        key,
        amount,
        _QUOTA_TTL_SECONDS,
    )
    return int(new_value)


async def get_current_usage(tenant_id: str, resource_type: str) -> int:
    """Read the current usage counter from Redis."""
    redis = await get_redis()
    key = _quota_key(tenant_id, resource_type)
    value = await redis.get(key)
    return int(value) if value else 0


async def reset_quota_counter(tenant_id: str, resource_type: str) -> None:
    """Reset the usage counter for a tenant+resource."""
    redis = await get_redis()
    key = _quota_key(tenant_id, resource_type)
    await redis.delete(key)


def check_quota(resource_type: str):
    """FastAPI dependency: reject with 429 if the tenant's quota is exceeded.

    Args:
        resource_type: One of ``model_calls``, ``storage``, ``users``, ``apps``.
    """

    async def _checker(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
        # Super-admins and service keys bypass quota
        if "*" in ctx.permissions or ctx.is_superadmin:
            return ctx

        tenant_id = str(ctx.tenant_id)
        limit = await _get_tenant_quota_limit(tenant_id, resource_type)
        if limit is None:
            return ctx  # No limit configured

        current = await get_current_usage(tenant_id, resource_type)
        if current >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Quota exceeded for {resource_type}: {current}/{limit}",
            )
        return ctx

    return _checker


async def invalidate_quota_config_cache(tenant_id: str) -> None:
    """Call when tenant quota config changes."""
    redis = await get_redis()
    await redis.delete(f"aip:tenant_quota_config:{tenant_id}")
