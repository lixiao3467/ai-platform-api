"""Rate limiting middleware using Redis token bucket."""

from __future__ import annotations

import structlog
from fastapi import Request, status
from fastapi.responses import ORJSONResponse
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

        # Check rate limit BEFORE processing the request (using atomic Lua script)
        try:
            redis = await get_redis()
            # Atomic check-and-increment to avoid race conditions
            lua_script = """
            local current = redis.call('INCR', KEYS[1])
            if current == 1 then
                redis.call('EXPIRE', KEYS[1], ARGV[1])
            end
            local ttl = redis.call('TTL', KEYS[1])
            return {current, ttl}
            """
            result = await redis.eval(lua_script, 1, redis_key, settings.rate_limit_window_seconds)
            current = int(result[0])  # type: ignore[index]
            ttl = int(result[1])  # type: ignore[index]
        except Exception as e:
            # Redis failure should not block requests — fail open
            logger.warning("Rate limit check failed, allowing request", error=str(e))
            response = await call_next(request)
            return response

        if current > settings.rate_limit_default:
            logger.warning(
                "Rate limit exceeded",
                key=key_prefix,
                endpoint=endpoint,
                current=current,
            )
            return ORJSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "code": 429,
                    "error": "RATE_LIMITED",
                    "message": f"Rate limit exceeded: {settings.rate_limit_default} requests per {settings.rate_limit_window_seconds}s",
                    "retry_after": ttl,
                },
                headers={
                    "X-RateLimit-Limit": str(settings.rate_limit_default),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(ttl),
                    "Retry-After": str(ttl),
                },
            )

        # Process the request
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_default)
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, settings.rate_limit_default - current)
        )
        response.headers["X-RateLimit-Reset"] = str(ttl)

        return response
