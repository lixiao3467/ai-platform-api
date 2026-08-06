"""API Key management — /api/v1/api-keys/*.

Provides CRUD operations for application-level API keys.
Keys are generated with a `aiplat_` prefix and stored as SHA-256 hashes.
The raw key is only returned once at creation time.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.domain.models import ApiKey, App
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class ApiKeyCreateRequest(BaseModel):
    app_id: str = Field(description="Application ID to associate the key with")
    name: str | None = Field(default=None, max_length=64, description="Human-readable name")
    permissions: list[str] = Field(default_factory=list, description="Permission scopes")
    rate_limit: int = Field(default=1000, ge=1, le=100000, description="Rate limit per minute")
    expires_at: str | None = Field(default=None, description="Expiry time (ISO 8601) or null for no expiry")


class ApiKeyUpdateRequest(BaseModel):
    name: str | None = None
    permissions: list[str] | None = None
    rate_limit: int | None = None
    is_enabled: bool | None = None
    expires_at: str | None = None


class ApiKeyCreatedOut(BaseModel):
    """Response for key creation — includes the raw key (only time it's visible)."""

    id: str
    key: str
    key_prefix: str
    name: str | None
    app_id: str
    permissions: list[str]
    rate_limit: int
    expires_at: str | None
    created_at: str


class ApiKeyOut(BaseModel):
    """List response — does NOT include the raw key."""

    id: str
    app_id: str
    app_name: str | None = None
    name: str | None
    key_prefix: str
    permissions: list[str]
    rate_limit: int
    is_enabled: bool
    expires_at: str | None
    last_used_at: str | None
    created_at: str


class ApiKeyStatsOut(BaseModel):
    """Usage statistics for an API key."""

    key_id: str
    key_prefix: str
    total_requests_24h: int
    total_requests_7d: int
    total_requests_30d: int
    last_used_at: str | None


# =============================================================================
# Key Generation
# =============================================================================


def _generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns:
        (raw_key, key_prefix, key_hash)
    """
    raw_key = f"aiplat_{secrets.token_urlsafe(48)}"
    key_prefix = raw_key[:12]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_prefix, key_hash


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "/",
    response_model=ApiResponse[PaginatedResponse[ApiKeyOut]],
    summary="获取 API Key 列表",
    description="获取当前租户所有 API Key（不包含原始密钥）。",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def list_api_keys(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    app_id: str | None = Query(default=None, description="按应用过滤"),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List all API keys for the current tenant."""
    conditions = [App.tenant_id == ctx.tenant_id]
    if ctx.app_id:
        conditions.append(ApiKey.app_id == ctx.app_id)
    if app_id:
        conditions.append(ApiKey.app_id == uuid.UUID(app_id))

    # Count total
    from sqlalchemy import join
    join_clause = join(ApiKey, App, ApiKey.app_id == App.id)
    count_stmt = select(func.count()).select_from(join_clause).where(*conditions)
    total = (await session.execute(count_stmt)).scalar() or 0

    # Fetch page
    offset = (page - 1) * page_size
    stmt = (
        select(ApiKey, App.name.label("app_name"))
        .join(App, ApiKey.app_id == App.id)
        .where(*conditions)
        .order_by(ApiKey.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    rows = result.all()

    items = [
        ApiKeyOut(
            id=str(k.id),
            app_id=str(k.app_id),
            app_name=app_name,
            name=k.name,
            key_prefix=k.key_prefix,
            permissions=k.permissions or [],
            rate_limit=k.rate_limit,
            is_enabled=k.is_enabled if hasattr(k, 'is_enabled') else True,
            expires_at=k.expires_at.isoformat() if k.expires_at else None,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            created_at=k.created_at.isoformat(),
        )
        for k, app_name in rows
    ]

    return ApiResponse(
        data=PaginatedResponse(items=items, total=total, page=page, page_size=page_size)
    )


@router.post(
    "/",
    response_model=ApiResponse[ApiKeyCreatedOut],
    summary="创建 API Key",
    description="创建新的 API Key。原始密钥仅在此响应中返回一次，请妥善保存。",
    dependencies=[Depends(require_permission("apikey.manage"))],
    responses={
        200: {"description": "创建成功，返回包含原始密钥的响应"},
        404: {"description": "应用不存在"},
    },
)
async def create_api_key(
    req: ApiKeyCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new API key — returns the raw key ONLY in this response."""
    # Verify app exists and belongs to tenant
    app = await session.get(App, uuid.UUID(req.app_id))
    if not app or app.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="应用不存在")

    raw_key, key_prefix, key_hash = _generate_api_key()

    expires_at = None
    if req.expires_at:
        try:
            expires_at = datetime.fromisoformat(req.expires_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="expires_at 格式错误，需要 ISO 8601")

    api_key = ApiKey(
        id=uuid.uuid4(),
        app_id=app.id,
        key_prefix=key_prefix,
        key_hash=key_hash,
        name=req.name,
        permissions=req.permissions,
        rate_limit=req.rate_limit,
        expires_at=expires_at,
        is_enabled=True,
    )
    session.add(api_key)
    await session.flush()

    return ApiResponse(
        data=ApiKeyCreatedOut(
            id=str(api_key.id),
            key=raw_key,
            key_prefix=key_prefix,
            name=api_key.name,
            app_id=str(app.id),
            permissions=api_key.permissions or [],
            rate_limit=api_key.rate_limit,
            expires_at=api_key.expires_at.isoformat() if api_key.expires_at else None,
            created_at=api_key.created_at.isoformat(),
        )
    )


@router.put(
    "/{key_id}",
    response_model=ApiResponse[ApiKeyOut],
    summary="更新 API Key",
    description="更新 API Key 的名称、权限、速率限制或启用状态。",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def update_api_key(
    key_id: uuid.UUID,
    req: ApiKeyUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Update an API key's metadata (not the key itself)."""
    stmt = select(ApiKey, App.name).join(App).where(
        ApiKey.id == key_id,
        App.tenant_id == ctx.tenant_id,
    )
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    api_key, app_name = row

    if req.name is not None:
        api_key.name = req.name
    if req.permissions is not None:
        api_key.permissions = req.permissions
    if req.rate_limit is not None:
        api_key.rate_limit = req.rate_limit
    if req.is_enabled is not None:
        api_key.is_enabled = req.is_enabled
    if req.expires_at is not None:
        if req.expires_at == "":
            api_key.expires_at = None
        else:
            try:
                api_key.expires_at = datetime.fromisoformat(req.expires_at)
            except ValueError:
                raise HTTPException(status_code=422, detail="expires_at 格式错误")

    await session.flush()

    return ApiResponse(
        data=ApiKeyOut(
            id=str(api_key.id),
            app_id=str(api_key.app_id),
            app_name=app_name,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            permissions=api_key.permissions or [],
            rate_limit=api_key.rate_limit,
            is_enabled=api_key.is_enabled,
            expires_at=api_key.expires_at.isoformat() if api_key.expires_at else None,
            last_used_at=api_key.last_used_at.isoformat() if api_key.last_used_at else None,
            created_at=api_key.created_at.isoformat(),
        )
    )


@router.delete(
    "/{key_id}",
    response_model=ApiResponse,
    summary="删除 API Key",
    description="立即删除 API Key。使用该 Key 的集成将立即失效。",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def delete_api_key(
    key_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Delete (revoke) an API key."""
    stmt = select(ApiKey).join(App).where(
        ApiKey.id == key_id,
        App.tenant_id == ctx.tenant_id,
    )
    result = await session.execute(stmt)
    api_key = result.scalars().first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    # Also revoke from Redis cache
    try:
        from ai_platform.infra.cache.redis_client import get_redis
        redis = await get_redis()
        await redis.delete(f"aip:key:{api_key.key_prefix}")
    except Exception:
        pass  # Best effort

    await session.delete(api_key)
    return ApiResponse(message="API Key 已删除")


@router.post(
    "/{key_id}/toggle",
    response_model=ApiResponse[ApiKeyOut],
    summary="启用/禁用 API Key",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def toggle_api_key(
    key_id: uuid.UUID,
    enabled: bool = Query(..., description="true=启用, false=禁用"),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Enable or disable an API key."""
    stmt = select(ApiKey, App.name).join(App).where(
        ApiKey.id == key_id,
        App.tenant_id == ctx.tenant_id,
    )
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    api_key, app_name = row
    api_key.is_enabled = enabled
    await session.flush()

    # Invalidate Redis cache
    try:
        from ai_platform.infra.cache.redis_client import get_redis
        redis = await get_redis()
        await redis.delete(f"aip:key:{api_key.key_prefix}")
    except Exception:
        pass

    return ApiResponse(
        data=ApiKeyOut(
            id=str(api_key.id),
            app_id=str(api_key.app_id),
            app_name=app_name,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            permissions=api_key.permissions or [],
            rate_limit=api_key.rate_limit,
            is_enabled=api_key.is_enabled,
            expires_at=api_key.expires_at.isoformat() if api_key.expires_at else None,
            last_used_at=api_key.last_used_at.isoformat() if api_key.last_used_at else None,
            created_at=api_key.created_at.isoformat(),
        )
    )


@router.get(
    "/{key_id}/stats",
    response_model=ApiResponse[ApiKeyStatsOut],
    summary="API Key 使用统计",
    description="获取指定 API Key 的请求量统计（24h/7d/30d）。",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def get_api_key_stats(
    key_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get usage statistics for an API key."""
    from datetime import timedelta

    from ai_platform.domain.models import AuditLog

    # Verify key belongs to tenant
    stmt = select(ApiKey).join(App).where(
        ApiKey.id == key_id,
        App.tenant_id == ctx.tenant_id,
    )
    result = await session.execute(stmt)
    api_key = result.scalars().first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")

    now = datetime.now(tz=timezone.utc)
    prefix = api_key.key_prefix

    async def count_by_prefix(since: datetime) -> int:
        s = select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == ctx.tenant_id,
            AuditLog.api_key_prefix == prefix,
            AuditLog.created_at >= since,
        )
        return (await session.execute(s)).scalar() or 0

    h24 = await count_by_prefix(now - timedelta(hours=24))
    d7 = await count_by_prefix(now - timedelta(days=7))
    d30 = await count_by_prefix(now - timedelta(days=30))

    return ApiResponse(
        data=ApiKeyStatsOut(
            key_id=str(api_key.id),
            key_prefix=prefix,
            total_requests_24h=h24,
            total_requests_7d=d7,
            total_requests_30d=d30,
            last_used_at=api_key.last_used_at.isoformat() if api_key.last_used_at else None,
        )
    )
