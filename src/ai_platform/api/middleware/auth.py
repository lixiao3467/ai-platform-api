"""Authentication: JWT + API Key dual-mode middleware."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

from ai_platform.config import get_settings
from ai_platform.infra.cache.redis_client import get_redis

# Hard wall-clock cap for any single Redis call in the auth hot path.
# The redis client already has socket_timeout=2s, but connection establishment
# and a few edge cases can push individual calls past that. This keeps the
# entire fallback chain bounded.
_REDIS_OP_TIMEOUT_S = 2.0

logger = structlog.get_logger()


# =============================================================================
# In-process L1 cache (reduces pressure on Redis; TTL enforced manually)
# =============================================================================
# We use a simple dict + timestamps rather than functools.lru_cache because
# lru_cache cannot express per-key TTL and is awkward to invalidate from
# tests. The dataclass wrapper keeps the API tiny and explicit.


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float  # time.monotonic()

    def is_alive(self) -> bool:
        return time.monotonic() < self.expires_at


class _TTLCache:
    """Tiny in-process TTL cache (bounded, thread-safe enough for ASGI)."""

    def __init__(self, maxsize: int = 1000, default_ttl_s: float = 60.0) -> None:
        self._maxsize = maxsize
        self._default_ttl_s = default_ttl_s
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if not entry.is_alive():
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        if len(self._store) >= self._maxsize and key not in self._store:
            # Evict oldest-expiring entry (very coarse — good enough for L1).
            oldest_key = min(
                self._store,
                key=lambda k: self._store[k].expires_at,
            )
            self._store.pop(oldest_key, None)
        self._store[key] = _CacheEntry(
            value=value,
            expires_at=time.monotonic() + (ttl_s or self._default_ttl_s),
        )

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


# Module-level L1 caches (permission + tenant status)
_perm_cache = _TTLCache(maxsize=2000, default_ttl_s=60.0)
_tenant_status_cache = _TTLCache(maxsize=500, default_ttl_s=30.0)

# Sentinel value for negative API-key cache entries (key not found)
_NEGATIVE_CACHE_SENTINEL = {"__not_found__": True}


def _is_redis_error(exc: BaseException) -> bool:
    """Return True if *exc* is a Redis-side failure (connection, timeout, etc).

    We intentionally treat any Redis exception as "Redis is down" so the
    auth path falls back to DB instead of 500-ing on a transient blip.
    asyncio.TimeoutError (from our wait_for wrappers) is also treated as a
    Redis-side failure so the fallback path runs on deadline expiry.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    try:
        import redis  # type: ignore[import-not-found]

        return isinstance(exc, redis.exceptions.RedisError)
    except ImportError:
        # redis package missing (should not happen in practice) — be defensive
        return type(exc).__name__.startswith("Redis")


def _redis_unavailable(exc: BaseException, *, context: str) -> None:
    """Log a Redis degradation warning once per distinct error type."""
    logger.warning(
        "Redis unavailable — falling back to DB",
        context=context,
        error_type=type(exc).__name__,
        error=str(exc),
    )


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
    is_superadmin: bool = False
    active_role: str | None = None

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
    """Create a JWT access token with standard claims (exp/iat/nbf)."""
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "iss": "ai-platform",
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    tenant_id: str,
    user_id: str,
    *,
    jti: str | None = None,
) -> str:
    """Create a long-lived JWT refresh token.

    The refresh token carries a unique `jti` (JWT ID) that the server can
    revoke by adding it to a block-list in Redis.
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(days=settings.jwt_refresh_expire_days),
        "iss": "ai-platform",
        "type": "refresh",
        "jti": jti or str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


async def revoke_refresh_token(jti: str) -> None:
    """Add a refresh token JTI to the Redis revocation block-list."""
    settings = get_settings()
    redis = await get_redis()
    # TTL matches max refresh token lifetime + 1 day buffer
    ttl = (settings.jwt_refresh_expire_days + 1) * 86400
    await asyncio.wait_for(redis.setex(f"aip:rt_revoked:{jti}", ttl, "1"), timeout=_REDIS_OP_TIMEOUT_S)


async def is_refresh_token_revoked(jti: str) -> bool:
    """Check whether a refresh token JTI has been revoked."""
    redis = await get_redis()
    return bool(await asyncio.wait_for(redis.get(f"aip:rt_revoked:{jti}"), timeout=_REDIS_OP_TIMEOUT_S))


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
                "verify_iss": True,
                "issuer": "ai-platform",
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

    1. Reject malformed keys early
    2. Check in-process L1 cache (including negative cache for invalid keys)
    3. Check Redis cache (prefix -> cached metadata)
    4. If not cached, look up in database by prefix, verify hash
    5. Cache result for 5 minutes (Redis best-effort; L1 as backup)

    Returns key metadata dict or None if invalid.
    """
    import hashlib

    # Reject malformed keys early (before any cache/DB lookup)
    if not raw_key.startswith("aiplat_"):
        return None

    prefix = raw_key[:8] if len(raw_key) >= 8 else raw_key
    cache_key = f"aip:key:{prefix}"

    # L1: in-process cache (includes negative cache)
    l1_hit = _perm_cache.get(cache_key)
    if l1_hit is not None:
        if isinstance(l1_hit, dict) and l1_hit.get("__not_found__"):
            return None  # Negative cache hit — key was recently invalid
        return l1_hit

    # L2: Redis cache (degrade to DB on failure)
    try:
        redis = await get_redis()
        cached = await asyncio.wait_for(redis.get(cache_key), timeout=_REDIS_OP_TIMEOUT_S)
        if cached:
            metadata = json.loads(cached)
            # Check for negative cache sentinel in Redis too
            if isinstance(metadata, dict) and metadata.get("__not_found__"):
                _perm_cache.set(cache_key, _NEGATIVE_CACHE_SENTINEL, ttl_s=30.0)
                return None
            _perm_cache.set(cache_key, metadata, ttl_s=300.0)
            return metadata
    except Exception as exc:
        if _is_redis_error(exc):
            _redis_unavailable(exc, context="verify_api_key")
        else:
            raise

    # L3: Real DB lookup by key_hash
    from sqlalchemy import select

    from ai_platform.domain.models import ApiKey
    from ai_platform.infra.database.connection import get_session_factory

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    stmt = select(ApiKey).where(
        ApiKey.key_hash == key_hash,
        ApiKey.key_prefix == prefix,
    )

    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(stmt)
        api_key_obj = result.scalars().first()

        if not api_key_obj:
            # Cache negative result for 30s to prevent DB-DoS from invalid keys
            _perm_cache.set(cache_key, _NEGATIVE_CACHE_SENTINEL, ttl_s=30.0)
            return None

        # Check is_enabled
        if not getattr(api_key_obj, "is_enabled", True):
            _perm_cache.set(cache_key, _NEGATIVE_CACHE_SENTINEL, ttl_s=30.0)
            return None

        # Check expiry
        if api_key_obj.expires_at:
            now = datetime.now(tz=timezone.utc)
            expires = api_key_obj.expires_at
            if isinstance(expires, datetime) and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if isinstance(expires, datetime) and expires < now:
                _perm_cache.set(cache_key, _NEGATIVE_CACHE_SENTINEL, ttl_s=30.0)
                return None

        # Touch last_used_at in a dedicated transaction (best-effort)
        try:
            from sqlalchemy import update
            async with factory(begin=True) as touch_session:
                await touch_session.execute(
                    update(ApiKey).where(ApiKey.id == api_key_obj.id).values(
                        last_used_at=datetime.now(tz=timezone.utc)
                    )
                )
        except Exception:
            logger.debug("Failed to update last_used_at for API key", exc_info=True)

        # Resolve tenant_id via app
        from sqlalchemy.orm import selectinload

        app_result = await session.execute(
            select(ApiKey).where(ApiKey.id == api_key_obj.id).options(selectinload(ApiKey.app))
        )
        api_key_obj = app_result.scalars().first()

        tenant_id = str(api_key_obj.app.tenant_id) if api_key_obj and api_key_obj.app else None
        if not tenant_id:
            return None

        metadata = {
            "app_id": str(api_key_obj.app_id),
            "tenant_id": tenant_id,
            "permissions": api_key_obj.permissions or [],
        }

        # Cache in L1 and best-effort in Redis
        _perm_cache.set(cache_key, metadata, ttl_s=300.0)
        try:
            redis = await get_redis()
            await asyncio.wait_for(
                redis.setex(cache_key, 300, json.dumps(metadata)),
                timeout=_REDIS_OP_TIMEOUT_S,
            )
        except Exception as exc:
            if _is_redis_error(exc):
                _redis_unavailable(exc, context="verify_api_key:cache_set")
            else:
                raise
        return metadata


# =============================================================================
# Permission Loading (from DB)
# =============================================================================


async def _load_user_permissions(user_id: str, tenant_id: uuid.UUID) -> tuple[list[str], bool]:
    """Load permissions for a JWT-authenticated user from the database.

    Traverses: user → user_roles → roles → role_permissions → permissions

    Returns:
        (permissions_list, is_superadmin)
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from ai_platform.domain.models import Role, User
    from ai_platform.infra.database.connection import get_session_factory

    # Validate user_id is a valid UUID before DB query
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, AttributeError):
        logger.warning("Invalid user_id format in JWT", user_id=user_id)
        return [], False

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(User)
            .where(User.id == user_uuid, User.tenant_id == tenant_id)
            .options(
                selectinload(User.roles).selectinload(Role.permissions)
            )
        )
        result = await session.execute(stmt)
        user = result.scalars().first()

        if not user:
            return [], False

        if user.is_superadmin:
            return ["*"], True

        # Collect unique permissions from all roles
        perm_set: set[str] = set()
        for role in user.roles:
            for perm in role.permissions:
                # Format: resource.action  (e.g. "agent.write")
                perm_str = f"{perm.resource}.{perm.action}"
                perm_set.add(perm_str)

        return list(perm_set), False


async def _get_role_permissions(role_code: str, tenant_id: uuid.UUID) -> list[str] | None:
    """Load permission strings for a single role (identified by code + tenant).

    Returns None when no matching role is found so the caller can decide how
    to degrade (we fall back to the user's full permission set).
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from ai_platform.domain.models import Role
    from ai_platform.infra.database.connection import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(Role)
            .where(Role.code == role_code, Role.tenant_id == tenant_id)
            .options(selectinload(Role.permissions))
        )
        result = await session.execute(stmt)
        role = result.scalars().first()
        if not role:
            return None
        return [f"{p.resource}.{p.action}" for p in role.permissions]


async def _check_tenant_status(tenant_id: uuid.UUID) -> str:
    """Check tenant status, using a layered cache to avoid DB lookup on every request.

    Cache layers:
        L1 — in-process (fastest, ~60s TTL)
        L2 — Redis (300s TTL)
        L3 — DB (source of truth)

    Redis failures degrade gracefully to the DB path; the request stays alive.

    Returns the tenant status string.
    Raises 403 if tenant is not active.
    """
    cache_key = f"aip:tenant_status:{tenant_id}"

    # L1: in-process cache
    l1_hit = _tenant_status_cache.get(cache_key)
    if l1_hit is not None:
        if l1_hit != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Tenant is {l1_hit}",
            )
        return l1_hit

    # L2: Redis
    tenant_status: str | None = None
    try:
        redis = await get_redis()
        cached = await asyncio.wait_for(redis.get(cache_key), timeout=_REDIS_OP_TIMEOUT_S)
        if cached:
            tenant_status = cached
            _tenant_status_cache.set(cache_key, cached, ttl_s=60.0)
    except Exception as exc:
        if _is_redis_error(exc):
            _redis_unavailable(exc, context="_check_tenant_status")
        else:
            raise

    # L3: DB
    if tenant_status is None:
        from sqlalchemy import select

        from ai_platform.domain.models import Tenant
        from ai_platform.infra.database.connection import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(select(Tenant.status).where(Tenant.id == tenant_id))
            tenant_status = result.scalar_one_or_none()

        if tenant_status is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )

        # Populate caches (Redis best-effort)
        _tenant_status_cache.set(cache_key, tenant_status, ttl_s=60.0)
        try:
            redis = await get_redis()
            await asyncio.wait_for(
                redis.setex(cache_key, 300, tenant_status),
                timeout=_REDIS_OP_TIMEOUT_S,
            )
        except Exception as exc:
            if _is_redis_error(exc):
                _redis_unavailable(exc, context="_check_tenant_status:cache_set")
            else:
                raise

    if tenant_status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant is {tenant_status}",
        )

    return tenant_status


async def invalidate_tenant_status_cache(tenant_id: uuid.UUID) -> None:
    """Invalidate the tenant status cache — call when tenant status changes."""
    cache_key = f"aip:tenant_status:{tenant_id}"
    _tenant_status_cache.delete(cache_key)
    try:
        redis = await get_redis()
        await asyncio.wait_for(redis.delete(cache_key), timeout=_REDIS_OP_TIMEOUT_S)
    except Exception as exc:
        if _is_redis_error(exc):
            _redis_unavailable(exc, context="invalidate_tenant_status_cache")
        else:
            raise


# =============================================================================
# FastAPI Dependencies
# =============================================================================


async def get_request_context(request: Request) -> RequestContext:
    """
    Extract and validate authentication from the request.

    Supports two modes:
    - Authorization: Bearer <JWT>
    - X-API-Key: <key>

    Both paths:
    - Check tenant status (active / suspended / cancelled)
    - Load permissions for the authenticated identity
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
        # Validate tenant_id format (prevent 500 on malformed data)
        try:
            tenant_id = uuid.UUID(key_meta["tenant_id"])
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Invalid tenant_id in API key metadata", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key configuration",
            )

        # Check tenant status
        await _check_tenant_status(tenant_id)

        ctx = RequestContext(
            tenant_id=tenant_id,
            app_id=uuid.UUID(key_meta["app_id"]) if key_meta.get("app_id") else None,
            api_key_prefix=api_key[:8],
            permissions=key_meta.get("permissions", []),
            trace_id=trace_id,
        )
        request.state.auth_context = ctx
        return ctx

    # --- Try JWT ---
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = decode_jwt_token(token)

        # Validate tenant_id format (prevent 500 on malformed JWT claims)
        try:
            tenant_id = uuid.UUID(payload["tenant_id"])
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Invalid tenant_id in JWT payload", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token claims",
            )

        user_id = payload.get("sub")

        # Check tenant status
        await _check_tenant_status(tenant_id)

        # Load user permissions from DB (with caching)
        permissions: list[str] = []
        is_superadmin = False
        active_role: str | None = None

        if user_id:
            # Determine active role selection (stored in Redis)
            try:
                redis = await get_redis()
                active_role = await asyncio.wait_for(
                    redis.get(f"aip:user:{user_id}:active_role"),
                    timeout=_REDIS_OP_TIMEOUT_S,
                )
            except Exception as exc:
                if _is_redis_error(exc):
                    _redis_unavailable(exc, context="get_request_context:active_role")
                    # Fail open for active_role — user keeps full permissions
                else:
                    raise

            # Cache key includes active_role so different role selections
            # get their own L1 cache entry.
            role_suffix = f":{active_role}" if active_role else ""
            perm_cache_key = f"aip:user_perms:{user_id}{role_suffix}"

            # L1: in-process cache
            l1_hit = _perm_cache.get(perm_cache_key)
            perm_data: dict | None = None
            if l1_hit is not None:
                perm_data = l1_hit
            else:
                # L2: Redis (degrade to DB on failure)
                try:
                    redis = await get_redis()
                    cached_perms = await asyncio.wait_for(
                        redis.get(perm_cache_key),
                        timeout=_REDIS_OP_TIMEOUT_S,
                    )
                    if cached_perms:
                        perm_data = json.loads(cached_perms)
                        _perm_cache.set(perm_cache_key, perm_data, ttl_s=300.0)
                except Exception as exc:
                    if _is_redis_error(exc):
                        _redis_unavailable(exc, context="get_request_context:perms")
                    else:
                        raise

            if perm_data is None:
                # L3: DB — always loads the FULL permission set across all roles
                permissions, is_superadmin = await _load_user_permissions(user_id, tenant_id)

                # If an active role is selected, scope permissions to that role
                if active_role and not is_superadmin:
                    role_perms = await _get_role_permissions(active_role, tenant_id)
                    if role_perms is not None:
                        # Intersect: only permissions the role has AND user has
                        full_set = set(permissions)
                        permissions = [p for p in role_perms if p in full_set]
                    else:
                        # Role not found — deny access (fail closed, not open)
                        logger.warning(
                            "Active role not found, denying access",
                            user_id=user_id,
                            active_role=active_role,
                        )
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Role '{active_role}' not found. Please switch to a valid role.",
                        )

                perm_data = {"permissions": permissions, "is_superadmin": is_superadmin}
                _perm_cache.set(perm_cache_key, perm_data, ttl_s=300.0)
                # Best-effort Redis populate
                try:
                    redis = await get_redis()
                    await asyncio.wait_for(
                        redis.setex(perm_cache_key, 300, json.dumps(perm_data)),
                        timeout=_REDIS_OP_TIMEOUT_S,
                    )
                except Exception as exc:
                    if _is_redis_error(exc):
                        _redis_unavailable(exc, context="get_request_context:perms:cache_set")
                    else:
                        raise

            permissions = perm_data.get("permissions", [])
            is_superadmin = perm_data.get("is_superadmin", False)

        ctx = RequestContext(
            tenant_id=tenant_id,
            user_id=user_id,
            permissions=permissions,
            trace_id=trace_id,
            is_superadmin=is_superadmin,
            active_role=active_role,
        )
        request.state.auth_context = ctx
        return ctx

    # --- No auth provided ---
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authentication. Provide 'Authorization: Bearer <JWT>' or 'X-API-Key: <key>'",
    )


async def optional_auth(request: Request) -> RequestContext | None:
    """Optional authentication — returns None if no auth provided.

    Only swallows 401 (missing/invalid credentials). Other errors like
    403 (tenant suspended) are re-raised.
    """
    try:
        return await get_request_context(request)
    except HTTPException as exc:
        # Only swallow 401 (missing auth); re-raise 403 (tenant suspended) etc.
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        raise
