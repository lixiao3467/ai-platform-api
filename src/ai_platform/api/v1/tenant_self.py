"""Tenant Self-Service API — /api/v1/tenant/self/*.

Provides endpoints for tenant administrators to manage their own tenant:
- View/update tenant info
- View usage / quota
- Manage members (invite, remove, change roles)
- View available models
- View audit logs (scoped to own tenant)
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.api.v1._shared import IdRequest
from ai_platform.domain.models import (
    ApiKey,
    App,
    AuditLog,
    Role,
    Tenant,
    User,
    role_permissions,
    user_roles,
)
from ai_platform.infra.database.connection import get_db
from ai_platform.services.model_resolver import VALID_PURPOSES, ModelResolverService

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class TenantSelfOut(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    status: str
    admin_email: str | None = None
    max_users: int = 10
    max_apps: int = 5
    max_api_keys_per_app: int = 10
    allowed_features: list = Field(default_factory=list)
    custom_domain: str | None = None
    created_at: str
    updated_at: str


class TenantSelfUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    admin_email: str | None = Field(default=None, max_length=256)
    custom_domain: str | None = Field(default=None, max_length=256)


class UsageOut(BaseModel):
    current_users: int
    max_users: int
    current_apps: int
    max_apps: int
    quota_config: dict


class MemberOut(BaseModel):
    id: str
    username: str
    email: str
    display_name: str | None
    is_active: bool
    roles: list[dict]
    last_login_at: str | None
    created_at: str


class MemberInviteRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    email: str = Field(max_length=128)
    password: str = Field(min_length=6)
    display_name: str | None = None
    role_ids: list[str] = Field(default_factory=list)


class MemberRemoveRequest(BaseModel):
    id: str


class MemberRoleUpdateRequest(BaseModel):
    id: str
    role_ids: list[str]


class ModelListRequest(BaseModel):
    purpose: str | None = None


class AuditLogListRequest(BaseModel):
    page: int = 1
    page_size: int = 20
    action: str | None = None


class ModelAccessOut(BaseModel):
    model_id: str
    is_enabled: bool
    rate_limit: int | None = None
    quota_limit: int | None = None
    quota_used: int = 0


class TenantAvailableModel(BaseModel):
    """Aggregated model info returned by ``/self/models``.

    Flattens each ``(provider, model)`` pair into one entry, so the front-end
    can render a single list for "which models can I use?".

    Backward compatible with the legacy ``ModelAccessOut`` shape — old fields
    are kept (with best-effort values) while new fields expose the richer
    provider-aware data.
    """

    # --- New shape (front-end canonical) ---
    name: str
    provider: str
    display_name: str | None = None
    status: str = "available"  # "available" | "unavailable"
    quota_remaining: int | None = None

    # --- Purpose tags (llm / embedding / vision / …) ---
    purposes: list[str] = Field(default_factory=list)

    # --- Legacy compatibility (kept so older front-ends keep working) ---
    model_id: str | None = None
    is_enabled: bool = True
    rate_limit: int | None = None
    quota_limit: int | None = None
    quota_used: int = 0


class AuditLogOut(BaseModel):
    id: str
    tenant_id: str
    user_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    status_code: int | None
    created_at: str


# =============================================================================
# Tenant API Key schemas (self-service)
# =============================================================================


class TenantApiKeyOut(BaseModel):
    """API key as seen by the tenant self-service UI (raw key never included)."""

    id: str
    name: str | None
    key_prefix: str
    permissions: list[str]
    allowed_models: list[str]
    ip_whitelist: list[str]
    expires_at: str | None
    last_used_at: str | None
    created_at: str
    is_enabled: bool


class TenantApiKeyListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class TenantApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=list)
    allowed_models: list[str] | None = None
    ip_whitelist: list[str] | None = None
    expires_at: str | None = Field(default=None, description="ISO 8601 或 null")


class TenantApiKeyUpdateRequest(BaseModel):
    id: str
    name: str | None = Field(default=None, max_length=64)
    permissions: list[str] | None = None
    allowed_models: list[str] | None = None
    ip_whitelist: list[str] | None = None
    expires_at: str | None = None


class TenantApiKeyCreateResponse(BaseModel):
    """Create response — includes the raw key (only time it is visible)."""

    id: str
    key: str
    key_prefix: str
    name: str | None


class TenantApiKeyRotateResponse(BaseModel):
    """Rotate response — includes the new raw key (only time it is visible)."""

    id: str
    new_key: str
    key_prefix: str


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "/self/get",
    response_model=ApiResponse[TenantSelfOut],
    summary="获取当前租户信息",
    dependencies=[Depends(require_permission("tenant.config"))],
)
async def get_tenant_self(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Return the current tenant's own information."""
    tenant = await session.get(Tenant, ctx.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return ApiResponse(data=TenantSelfOut(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        plan=tenant.plan,
        status=tenant.status,
        admin_email=getattr(tenant, "admin_email", None),
        max_users=getattr(tenant, "max_users", 10),
        max_apps=getattr(tenant, "max_apps", 5),
        max_api_keys_per_app=getattr(tenant, "max_api_keys_per_app", 10),
        allowed_features=getattr(tenant, "allowed_features", []),
        custom_domain=getattr(tenant, "custom_domain", None),
        created_at=tenant.created_at.isoformat(),
        updated_at=tenant.updated_at.isoformat(),
    ))


@router.post(
    "/self/update",
    response_model=ApiResponse[TenantSelfOut],
    summary="更新租户基本信息",
    dependencies=[Depends(require_permission("tenant.config"))],
)
async def update_tenant_self(
    req: TenantSelfUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Update the current tenant's basic info (name, admin email, custom domain)."""
    tenant = await session.get(Tenant, ctx.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if req.name is not None:
        tenant.name = req.name
    if req.admin_email is not None:
        tenant.admin_email = req.admin_email
    if req.custom_domain is not None:
        tenant.custom_domain = req.custom_domain

    await session.flush()

    return ApiResponse(data=TenantSelfOut(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        plan=tenant.plan,
        status=tenant.status,
        admin_email=getattr(tenant, "admin_email", None),
        max_users=getattr(tenant, "max_users", 10),
        max_apps=getattr(tenant, "max_apps", 5),
        max_api_keys_per_app=getattr(tenant, "max_api_keys_per_app", 10),
        allowed_features=getattr(tenant, "allowed_features", []),
        custom_domain=getattr(tenant, "custom_domain", None),
        created_at=tenant.created_at.isoformat(),
        updated_at=tenant.updated_at.isoformat(),
    ))


@router.post(
    "/self/usage",
    response_model=ApiResponse[UsageOut],
    summary="查看自己的用量",
    dependencies=[Depends(require_permission("tenant.quota_view"))],
)
async def get_tenant_usage(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Return current usage counts vs. configured limits."""
    tenant = await session.get(Tenant, ctx.tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Count current users
    user_count = (await session.execute(
        select(func.count()).select_from(User).where(User.tenant_id == ctx.tenant_id)
    )).scalar() or 0

    # Count current apps
    app_count = (await session.execute(
        select(func.count()).select_from(App).where(App.tenant_id == ctx.tenant_id)
    )).scalar() or 0

    return ApiResponse(data=UsageOut(
        current_users=user_count,
        max_users=getattr(tenant, "max_users", 10),
        current_apps=app_count,
        max_apps=getattr(tenant, "max_apps", 5),
        quota_config=tenant.quota_config or {},
    ))


@router.post(
    "/self/members/list",
    response_model=ApiResponse[PaginatedResponse[MemberOut]],
    summary="查看成员列表",
    dependencies=[Depends(require_permission("user.manage"))],
)
async def list_members(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List all members (users) in the current tenant."""
    page = 1
    page_size = 20
    offset = (page - 1) * page_size
    stmt = (
        select(User)
        .where(User.tenant_id == ctx.tenant_id)
        .options(selectinload(User.roles))
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    users = result.scalars().all()

    total = (await session.execute(
        select(func.count()).select_from(User).where(User.tenant_id == ctx.tenant_id)
    )).scalar() or 0

    items = [
        MemberOut(
            id=str(u.id),
            username=u.username,
            email=u.email,
            display_name=u.display_name,
            is_active=u.is_active,
            roles=[{"id": str(r.id), "name": r.name} for r in u.roles],
            last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            created_at=u.created_at.isoformat(),
        )
        for u in users
    ]

    return ApiResponse(data=PaginatedResponse(items=items, total=total, page=page, page_size=page_size))


@router.post(
    "/self/members/invite",
    response_model=ApiResponse[MemberOut],
    summary="邀请成员",
    status_code=201,
    dependencies=[Depends(require_permission("user.manage"))],
)
async def invite_member(
    req: MemberInviteRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new user in the current tenant."""
    import bcrypt

    # Check max_users quota
    tenant = await session.get(Tenant, ctx.tenant_id)
    if tenant:
        max_users = getattr(tenant, "max_users", 10)
        current_count = (await session.execute(
            select(func.count()).select_from(User).where(User.tenant_id == ctx.tenant_id)
        )).scalar() or 0
        if current_count >= max_users:
            raise HTTPException(status_code=429, detail=f"User quota exceeded ({current_count}/{max_users})")

    # Check uniqueness
    existing = await session.execute(
        select(User).where(User.username == req.username)
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Username already exists")

    existing_email = await session.execute(
        select(User).where(User.email == req.email)
    )
    if existing_email.scalars().first():
        raise HTTPException(status_code=409, detail="Email already exists")

    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()

    user = User(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        username=req.username,
        email=req.email,
        password_hash=password_hash,
        display_name=req.display_name,
        is_active=True,
    )
    session.add(user)

    # Assign roles
    if req.role_ids:
        for role_id_str in req.role_ids:
            role = await session.get(Role, uuid.UUID(role_id_str))
            if role and (role.tenant_id == ctx.tenant_id or role.tenant_id is None):
                user.roles.append(role)

    await session.flush()

    return ApiResponse(data=MemberOut(
        id=str(user.id),
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=[{"id": str(r.id), "name": r.name} for r in user.roles],
        last_login_at=None,
        created_at=user.created_at.isoformat(),
    ))


@router.post(
    "/self/members/remove",
    response_model=ApiResponse,
    summary="移除成员",
    dependencies=[Depends(require_permission("user.manage"))],
)
async def remove_member(
    req: MemberRemoveRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Remove a user from the current tenant."""
    user_id = uuid.UUID(req.id)
    user = await session.get(User, user_id)
    if not user or user.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Member not found")

    # Prevent removing yourself
    if str(user.id) == ctx.user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    await session.delete(user)
    return ApiResponse(message="Member removed")


@router.post(
    "/self/members/update-role",
    response_model=ApiResponse[MemberOut],
    summary="修改成员角色",
    dependencies=[Depends(require_permission("user.manage"))],
)
async def update_member_role(
    req: MemberRoleUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Update a member's roles."""
    user_id = uuid.UUID(req.id)
    stmt = select(User).where(User.id == user_id, User.tenant_id == ctx.tenant_id).options(selectinload(User.roles))
    result = await session.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="Member not found")

    # Replace roles
    new_roles = []
    for role_id_str in req.role_ids:
        role = await session.get(Role, uuid.UUID(role_id_str))
        if role and (role.tenant_id == ctx.tenant_id or role.tenant_id is None):
            new_roles.append(role)
    user.roles = new_roles
    await session.flush()

    return ApiResponse(data=MemberOut(
        id=str(user.id),
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=[{"id": str(r.id), "name": r.name} for r in user.roles],
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        created_at=user.created_at.isoformat(),
    ))


@router.post(
    "/self/models/list",
    response_model=ApiResponse[list[TenantAvailableModel]],
    summary="查看可用模型",
    dependencies=[Depends(require_permission("model.read"))],
)
async def list_tenant_models(
    req: ModelListRequest = ModelListRequest(),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List models available to the current tenant.

    Aggregates from the provider configurations (``model_providers`` table)
    via :class:`ModelResolverService`.  Each ``(provider, model)`` pair
    becomes one entry in the response.

    Optional body field:
        ``purpose: "embedding"`` — only return models tagged with that purpose.
    """
    purpose = req.purpose
    # Validate purpose param early (so bad requests get a clean 400).
    if purpose is not None:
        if purpose not in VALID_PURPOSES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid purpose '{purpose}'. "
                    f"Valid values: {sorted(VALID_PURPOSES)}"
                ),
            )

    # --- Aggregated path (providers table is source of truth) ------------
    try:
        resolver = ModelResolverService(session)
        items = await resolver.list_available(ctx.tenant_id, purpose=purpose)

        if items:
            return ApiResponse(
                data=[
                    TenantAvailableModel(
                        name=item.model_name,
                        provider=item.provider_name,
                        display_name=item.provider_display,
                        status="available" if item.enabled else "unavailable",
                        quota_remaining=None,
                        purposes=item.purposes,
                        model_id=f"{item.provider_name}/{item.model_name}",
                        is_enabled=item.enabled,
                    )
                    for item in items
                ]
            )
    except Exception:
        # Fall through to legacy behaviour on unexpected errors so existing
        # tenants keep working even if the resolver misbehaves.
        pass

    # --- Legacy fallback (table may be empty / not migrated yet) ----------
    # Try to read from tenant_model_access (after migration)
    try:
        from sqlalchemy import text

        result = await session.execute(
            text(
                "SELECT model_id, is_enabled, rate_limit, quota_limit, quota_used "
                "FROM tenant_model_access WHERE tenant_id = :tid ORDER BY model_id"
            ),
            {"tid": str(ctx.tenant_id)},
        )
        rows = result.fetchall()
        if rows:
            return ApiResponse(
                data=[
                    TenantAvailableModel(
                        name=row[0],
                        provider="unknown",
                        display_name=None,
                        status="available" if row[1] else "unavailable",
                        quota_remaining=None,
                        purposes=[],
                        model_id=row[0],
                        is_enabled=row[1],
                        rate_limit=row[2],
                        quota_limit=row[3],
                        quota_used=row[4],
                    )
                    for row in rows
                ]
            )
    except Exception:
        pass  # Table may not exist yet

    # Fallback: return default model list based on plan
    tenant = await session.get(Tenant, ctx.tenant_id)
    plan = tenant.plan if tenant else "standard"

    default_models = {
        "free": ["gpt-3.5-turbo"],
        "standard": ["gpt-3.5-turbo", "gpt-4o", "qwen-plus"],
        "pro": [
            "gpt-4o",
            "gpt-4o-mini",
            "claude-3-sonnet",
            "qwen-plus",
            "deepseek-chat",
        ],
        "enterprise": [
            "gpt-4o",
            "gpt-4-turbo",
            "claude-3-opus",
            "claude-3-sonnet",
            "qwen-max",
            "deepseek-chat",
        ],
    }

    models = default_models.get(plan, default_models["standard"])
    return ApiResponse(
        data=[
            TenantAvailableModel(
                name=m,
                provider="default",
                display_name=None,
                status="available",
                quota_remaining=None,
                purposes=["llm", "chat"],
                model_id=m,
                is_enabled=True,
            )
            for m in models
        ]
    )


@router.post(
    "/self/audit-logs/list",
    response_model=ApiResponse[PaginatedResponse[AuditLogOut]],
    summary="查看自己的审计日志",
    dependencies=[Depends(require_permission("audit.view"))],
)
async def get_tenant_audit_logs(
    req: AuditLogListRequest = AuditLogListRequest(),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get audit logs scoped to the current tenant."""
    page = req.page
    page_size = req.page_size
    action = req.action

    conditions = [AuditLog.tenant_id == ctx.tenant_id]
    if action:
        conditions.append(AuditLog.action == action)

    offset = (page - 1) * page_size
    stmt = (
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    logs = result.scalars().all()

    count_stmt = select(func.count()).select_from(AuditLog).where(*conditions)
    total = (await session.execute(count_stmt)).scalar() or 0

    items = [
        AuditLogOut(
            id=str(log.id),
            tenant_id=str(log.tenant_id),
            user_id=log.user_id,
            action=log.action,
            resource_type=getattr(log, "resource_type", None),
            resource_id=getattr(log, "resource_id", None),
            status_code=getattr(log, "response_code", None),
            created_at=log.created_at.isoformat(),
        )
        for log in logs
    ]

    return ApiResponse(data=PaginatedResponse(items=items, total=total, page=page, page_size=page_size))


# =============================================================================
# Tenant API Key endpoints — /self/api-keys/*
# =============================================================================
# Tenant-scoped CRUD for the self-service API key UI. Unlike the global
# ``/api-keys/*`` routes, these keys carry a ``tenant_id`` directly (no app
# binding) and every query is constrained to the caller's tenant.


def _generate_tenant_api_key() -> tuple[str, str, str]:
    """Generate (raw_key, key_prefix, key_hash).

    Mirrors ``api_keys._generate_api_key`` so the key format stays consistent.
    """
    raw_key = f"aiplat_{secrets.token_urlsafe(48)}"
    key_prefix = raw_key[:12]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_prefix, key_hash


def _parse_expires_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="expires_at 格式错误，需要 ISO 8601")


def _tenant_api_key_to_out(k: ApiKey) -> TenantApiKeyOut:
    return TenantApiKeyOut(
        id=str(k.id),
        name=k.name,
        key_prefix=k.key_prefix,
        permissions=list(k.permissions or []),
        allowed_models=list(getattr(k, "allowed_models", None) or []),
        ip_whitelist=list(getattr(k, "ip_whitelist", None) or []),
        expires_at=k.expires_at.isoformat() if k.expires_at else None,
        last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
        created_at=k.created_at.isoformat(),
        is_enabled=bool(k.is_enabled),
    )


async def _get_tenant_api_key(session: AsyncSession, key_id: uuid.UUID, tenant_id: uuid.UUID) -> ApiKey:
    stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == tenant_id)
    api_key = (await session.execute(stmt)).scalars().first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    return api_key


@router.post(
    "/self/api-keys/list",
    response_model=ApiResponse[PaginatedResponse[TenantApiKeyOut]],
    summary="租户 API Key 列表",
    description="获取当前租户的所有 API Key（不含原始密钥）。",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def list_tenant_api_keys(
    req: TenantApiKeyListRequest = TenantApiKeyListRequest(),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List the current tenant's API keys (tenant-scoped, no app required)."""
    conditions = [ApiKey.tenant_id == ctx.tenant_id]

    total = (
        await session.execute(select(func.count()).select_from(ApiKey).where(*conditions))
    ).scalar() or 0

    offset = (req.page - 1) * req.page_size
    stmt = (
        select(ApiKey)
        .where(*conditions)
        .order_by(ApiKey.created_at.desc())
        .offset(offset)
        .limit(req.page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()

    items = [_tenant_api_key_to_out(k) for k in rows]
    return ApiResponse(
        data=PaginatedResponse(items=items, total=total, page=req.page, page_size=req.page_size)
    )


@router.post(
    "/self/api-keys/create",
    response_model=ApiResponse[TenantApiKeyCreateResponse],
    summary="创建租户 API Key",
    description="创建新的租户级 API Key。原始密钥仅在此响应中返回一次，请妥善保存。",
    status_code=201,
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def create_tenant_api_key(
    req: TenantApiKeyCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a tenant-level API key (no app binding)."""
    raw_key, key_prefix, key_hash = _generate_tenant_api_key()
    api_key = ApiKey(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        app_id=None,
        key_prefix=key_prefix,
        key_hash=key_hash,
        name=req.name,
        permissions=req.permissions,
        allowed_models=req.allowed_models or [],
        ip_whitelist=req.ip_whitelist or [],
        expires_at=_parse_expires_at(req.expires_at),
        is_enabled=True,
    )
    session.add(api_key)
    await session.flush()

    return ApiResponse(
        data=TenantApiKeyCreateResponse(
            id=str(api_key.id),
            key=raw_key,
            key_prefix=key_prefix,
            name=api_key.name,
        )
    )


@router.post(
    "/self/api-keys/update",
    response_model=ApiResponse[TenantApiKeyOut],
    summary="更新租户 API Key",
    description="更新 API Key 的名称、权限、可用模型、IP 白名单或过期时间。",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def update_tenant_api_key(
    req: TenantApiKeyUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Update a tenant API key's metadata (not the key value)."""
    api_key = await _get_tenant_api_key(session, uuid.UUID(req.id), ctx.tenant_id)

    if req.name is not None:
        api_key.name = req.name
    if req.permissions is not None:
        api_key.permissions = req.permissions
    if req.allowed_models is not None:
        api_key.allowed_models = req.allowed_models
    if req.ip_whitelist is not None:
        api_key.ip_whitelist = req.ip_whitelist
    if req.expires_at is not None:
        api_key.expires_at = _parse_expires_at(req.expires_at)

    await session.flush()
    return ApiResponse(data=_tenant_api_key_to_out(api_key))


@router.post(
    "/self/api-keys/delete",
    response_model=ApiResponse,
    summary="删除租户 API Key",
    description="立即删除 API Key。使用该 Key 的集成将立即失效。",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def delete_tenant_api_key(
    req: IdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Delete (revoke) a tenant API key."""
    api_key = await _get_tenant_api_key(session, uuid.UUID(req.id), ctx.tenant_id)

    # Best-effort Redis cache invalidation
    try:
        from ai_platform.infra.cache.redis_client import get_redis
        redis = await get_redis()
        await redis.delete(f"aip:key:{api_key.key_prefix}")
    except Exception:
        pass

    await session.delete(api_key)
    return ApiResponse(message="API Key 已删除")


@router.post(
    "/self/api-keys/rotate",
    response_model=ApiResponse[TenantApiKeyRotateResponse],
    summary="轮换租户 API Key",
    description="生成新的密钥值，保留原有元数据。新密钥仅返回一次。",
    dependencies=[Depends(require_permission("apikey.manage"))],
)
async def rotate_tenant_api_key(
    req: IdRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Rotate a tenant API key — replaces key_hash/key_prefix, keeps metadata.

    The old key stops working immediately (no grace-period retention, which
    would require an extra ``previous_key_hash`` column).
    """
    api_key = await _get_tenant_api_key(session, uuid.UUID(req.id), ctx.tenant_id)

    old_prefix = api_key.key_prefix
    raw_key, key_prefix, key_hash = _generate_tenant_api_key()
    api_key.key_prefix = key_prefix
    api_key.key_hash = key_hash
    await session.flush()

    # Invalidate cached entries for both old and new prefixes
    try:
        from ai_platform.infra.cache.redis_client import get_redis
        redis = await get_redis()
        await redis.delete(f"aip:key:{old_prefix}")
        await redis.delete(f"aip:key:{key_prefix}")
    except Exception:
        pass

    return ApiResponse(
        data=TenantApiKeyRotateResponse(
            id=str(api_key.id),
            new_key=raw_key,
            key_prefix=key_prefix,
        )
    )
