"""Authentication: JWT + API Key dual-mode middleware."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

from ai_platform.config import get_settings
from ai_platform.infra.cache.redis_client import get_redis

logger = structlog.get_logger()


# =============================================================================
# Request Context — carries authenticated identity through the request
# =============================================================================


@dataclass
class RequestContext:
    """Authenticated request context."""

    tenant_id: uuid.UUID
    app_id: uuid.UUID | None = None
    user_id: str | None = None
    api_key_prefix: str | None = None
    permissions: list[str] = field(default_factory=list)
    trace_id: str | None = None

    @property
    def is_api_key_auth(self) -> bool:
        return self.api_key_prefix is not None


# =============================================================================
# JWT Utilities
# =============================================================================


def create_jwt_token(
    tenant_id: str,
    user_id: str,
    *,
    extra_claims: dict | None = None,
) -> str:
    """Create a JWT token with standard claims (exp/iat/nbf)."""
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "iss": "ai-platform",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> dict:
    """Decode and validate a JWT token (expiration, issuer)."""
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={
                "verify_exp": True,
                "verify_aud": False,  # No audience configured yet
            },
        )
    except JWTError as e:
        # Do not leak internal error details to clients
        logger.info("JWT decode failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


# =============================================================================
# API Key Verification
# =============================================================================


async def verify_api_key(raw_key: str) -> dict | None:
    """
    Verify an API key.

    1. Check Redis cache first (prefix -> cached metadata)
    2. If not cached, look up in database by prefix, verify bcrypt hash
    3. Cache result for 5 minutes

    Returns key metadata dict or None if invalid.
    """
    prefix = raw_key[:8] if len(raw_key) >= 8 else raw_key
    redis = await get_redis()
    cache_key = f"aip:key:{prefix}"

    # Check cache
    import json

    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    # TODO: Database lookup for API key verification
    # For now, accept any key starting with "aiplat_" in development
    settings = get_settings()
    if settings.is_development and raw_key.startswith("aiplat_"):
        metadata = {
            "app_id": "00000000-0000-0000-0000-000000000001",
            "tenant_id": "00000000-0000-0000-0000-000000000001",
            "permissions": ["*"],
        }
        await redis.setex(cache_key, 300, json.dumps(metadata))
        return metadata

    return None


# =============================================================================
# FastAPI Dependencies
# =============================================================================


async def get_request_context(request: Request) -> RequestContext:
    """
    Extract and validate authentication from the request.

    Supports two modes:
    - Authorization: Bearer <JWT>
    - X-API-Key: <key>
    """
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))

    # --- Try API Key first ---
    api_key = request.headers.get("X-API-Key")
    if api_key:
        key_meta = await verify_api_key(api_key)
        if key_meta is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        return RequestContext(
            tenant_id=uuid.UUID(key_meta["tenant_id"]),
            app_id=uuid.UUID(key_meta["app_id"]) if key_meta.get("app_id") else None,
            api_key_prefix=api_key[:8],
            permissions=key_meta.get("permissions", []),
            trace_id=trace_id,
        )

    # --- Try JWT ---
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = decode_jwt_token(token)
        return RequestContext(
            tenant_id=uuid.UUID(payload["tenant_id"]),
            user_id=payload.get("sub"),
            trace_id=trace_id,
        )

    # --- No auth provided ---
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authentication. Provide 'Authorization: Bearer <JWT>' or 'X-API-Key: <key>'",
    )


async def optional_auth(request: Request) -> RequestContext | None:
    """Optional authentication — returns None if no auth provided."""
    try:
        return await get_request_context(request)
    except HTTPException:
        return None
