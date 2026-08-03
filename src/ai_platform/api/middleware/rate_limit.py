"""Rate limiting middleware using Redis token bucket."""

from __future__ import annotations

import structlog
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ai_platform.config import get_settings
from ai_platform.infra.cache.redis_client import get_redis

logger = structlog.get_logger()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis-based sliding window rate limiter.

    Dimensions: (tenant_id or IP, endpoint)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()

        # Skip rate limiting for health checks and public endpoints
        if request.url.path in (
            "/live", "/health", "/docs", "/redoc",
            "/openapi.json", "/metrics", "/favicon.ico",
        ):
            return await call_next(request)

        # Determine rate limit key
        api_key = request.headers.get("X-API-Key", "")
        client_ip = request.client.host if request.client else "unknown"
        key_prefix = api_key[:8] if api_key else client_ip
        endpoint = request.url.path
        redis_key = f"aip:ratelimit:{key_prefix}:{endpoint}"

        # Check rate limit
        redis = await get_redis()
        current = await redis.incr(redis_key)

        # Set TTL on first request in window
        if current == 1:
            await redis.expire(redis_key, settings.rate_limit_window_seconds)

        # Get remaining TTL
        ttl = await redis.ttl(redis_key)

        # Add rate limit headers
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_default)
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, settings.rate_limit_default - current)
        )
        response.headers["X-RateLimit-Reset"] = str(ttl)

        if current > settings.rate_limit_default:
            logger.warning(
                "Rate limit exceeded",
                key=key_prefix,
                endpoint=endpoint,
                current=current,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {settings.rate_limit_default} requests per {settings.rate_limit_window_seconds}s",
            )

        return response
