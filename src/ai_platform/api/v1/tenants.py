"""Tenant management API — /api/v1/tenants/* (super-admin CRUD)."""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.api.v1._shared import IdRequest
from ai_platform.domain.models import Role, Tenant, User
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class TenantListRequest(BaseModel):
    """Body for listing tenants with pagination, search, and filtering."""

    page: int = Field(default=1, ge=1, description="页码（从1开始）")
    page_size: int = Field(default=20, ge=1, le=100, description="每页条数")
    search: str | None = Field(default=None, description="按租户名称模糊搜索")
    status: str | None = Field(default=None, description="按状态过滤（active/disabled/...）")


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$")
    plan: str = Field(default="standard", max_length=32)
    admin_email: str | None = Field(default=None, max_length=128)
    quota_config: dict | None = None


class TenantUpdateRequest(BaseModel):
    id: str = Field(description="Tenant ID")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    plan: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=16)
    quota_config: dict | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        uuid.UUID(v)  # raises ValueError if invalid
        return v


class TenantOut(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    quota_config: dict | None = None
    status: str
    created_at: str
    updated_at: str


# =============================================================================
# Member schemas (admin manages ANY tenant by tenant_id in the body)
# =============================================================================


def _uuid_validator(v: str) -> str:
    uuid.UUID(v)
    return v


class TenantMembersListRequest(BaseModel):
    """Body for listing members of a specific tenant.

    The frontend (admin/Tenants.tsx) sends ``tenant_id`` (snake_case).
    """

    tenant_id: str = Field(description="目标租户 ID")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=500)

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v: str) -> str:
        return _uuid_validator(v)


class TenantMemberInviteRequest(BaseModel):
    """Body for adding a member to a tenant.

    The frontend sends ``tenantId`` (camelCase) plus ``email``, ``role`` and
    ``send_email``. We accept both the alias and the snake_case field name.
    """

    tenant_id: str = Field(alias="tenantId")
    email: str = Field(max_length=128)
    role: str = Field(description="角色 code，如 tenant_admin / tenant_developer / tenant_viewer")
    send_email: bool = True

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v: str) -> str:
        return _uuid_validator(v)


class TenantMemberRemoveRequest(BaseModel):
    """Body for removing a member — frontend sends ``tenantId`` + ``userId``."""

    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("tenant_id", "user_id")
    @classmethod
    def _validate_uuid(cls, v: str) -> str:
        return _uuid_validator(v)


class TenantMemberRoleUpdateRequest(BaseModel):
    """Body for updating a member's role — frontend sends
    ``tenantId`` + ``userId`` + ``role``."""

    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    role: str

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("tenant_id", "user_id")
    @classmethod
    def _validate_uuid(cls, v: str) -> str:
        return _uuid_validator(v)


class TenantMemberOut(BaseModel):
    """Member representation for the admin tenant members table."""

    id: str
    user_id: str
    username: str
    email: str
    role: str
    joined_at: str
    status: str


# =============================================================================
# Helpers
# =============================================================================


def _tenant_to_out(tenant: Tenant) -> TenantOut:
    return TenantOut(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        plan=tenant.plan or "standard",
        quota_config=tenant.quota_config,
        status=tenant.status or "active",
        created_at=tenant.created_at.isoformat(),
        updated_at=tenant.updated_at.isoformat(),
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "/list",
    response_model=ApiResponse[PaginatedResponse[TenantOut]],
    summary="租户列表",
    description="分页查询所有租户（超管视角）。支持按名称搜索、按状态过滤。",
    dependencies=[Depends(require_permission("tenant.read"))],
)
async def list_tenants(
    req: TenantListRequest = TenantListRequest(),
    session: AsyncSession = Depends(get_db),
):
    """分页租户列表 — 超管使用。"""
    conditions: list = []
    if req.search:
        conditions.append(Tenant.name.ilike(f"%{req.search}%"))
    if req.status:
        conditions.append(Tenant.status == req.status)

    where_clause = and_(*conditions) if conditions else True

    # Total count
    count_stmt = select(func.count()).select_from(Tenant).where(where_clause)
    total = (await session.execute(count_stmt)).scalar() or 0

    # Fetch page
    offset = (req.page - 1) * req.page_size
    stmt = (
        select(Tenant)
        .where(where_clause)
        .order_by(Tenant.created_at.desc())
        .offset(offset)
        .limit(req.page_size)
    )
    result = await session.execute(stmt)
    tenants = result.scalars().all()

    return ApiResponse(
        data=PaginatedResponse(
            items=[_tenant_to_out(t) for t in tenants],
            total=total,
            page=req.page,
            page_size=req.page_size,
        )
    )


@router.post(
    "/create",
    response_model=ApiResponse[TenantOut],
    summary="创建租户",
    description="创建一个新的租户。slug 必须全局唯一。",
    status_code=201,
    dependencies=[Depends(require_permission("tenant.create"))],
)
async def create_tenant(
    req: TenantCreateRequest,
    session: AsyncSession = Depends(get_db),
):
    """创建租户。"""
    # Check slug uniqueness
    existing = await session.execute(select(Tenant).where(Tenant.slug == req.slug))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"slug 已被占用: {req.slug}")

    tenant = Tenant(
        id=uuid.uuid4(),
        name=req.name,
        slug=req.slug,
        plan=req.plan,
        quota_config=req.quota_config or {},
        status="active",
    )
    session.add(tenant)
    await session.flush()

    # Reload to get server defaults (updated_at etc.)
    tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant.id))).scalars().first()

    return ApiResponse(data=_tenant_to_out(tenant))


@router.post(
    "/get",
    response_model=ApiResponse[TenantOut],
    summary="租户详情",
    description="获取单个租户详情。",
    dependencies=[Depends(require_permission("tenant.read"))],
)
async def get_tenant(
    req: IdRequest,
    session: AsyncSession = Depends(get_db),
):
    """租户详情。"""
    tenant = (await session.execute(select(Tenant).where(Tenant.id == uuid.UUID(req.id)))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")
    return ApiResponse(data=_tenant_to_out(tenant))


@router.post(
    "/update",
    response_model=ApiResponse[TenantOut],
    summary="更新租户",
    description="更新租户信息（名称、套餐、状态、配额等）。",
    dependencies=[Depends(require_permission("tenant.update"))],
)
async def update_tenant(
    req: TenantUpdateRequest,
    session: AsyncSession = Depends(get_db),
):
    """更新租户。"""
    tenant = (await session.execute(select(Tenant).where(Tenant.id == uuid.UUID(req.id)))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    if req.name is not None:
        tenant.name = req.name
    if req.plan is not None:
        tenant.plan = req.plan
    if req.status is not None:
        tenant.status = req.status
    if req.quota_config is not None:
        tenant.quota_config = req.quota_config

    await session.flush()

    tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant.id))).scalars().first()
    return ApiResponse(data=_tenant_to_out(tenant))


@router.post(
    "/delete",
    response_model=ApiResponse,
    summary="软删除租户",
    description="将租户状态设置为 disabled（软删除）。不会物理删除数据。",
    dependencies=[Depends(require_permission("tenant.delete"))],
)
async def delete_tenant(
    req: IdRequest,
    session: AsyncSession = Depends(get_db),
):
    """软删除租户 — 设置 status='disabled'。"""
    tenant = (await session.execute(select(Tenant).where(Tenant.id == uuid.UUID(req.id)))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    if tenant.status == "disabled":
        raise HTTPException(status_code=409, detail="租户已是禁用状态")

    tenant.status = "disabled"
    await session.flush()

    return ApiResponse(message="租户已禁用")


@router.post(
    "/enable",
    response_model=ApiResponse[TenantOut],
    summary="启用租户",
    description="将租户状态设置为 active。",
    dependencies=[Depends(require_permission("tenant.update"))],
)
async def enable_tenant(
    req: IdRequest,
    session: AsyncSession = Depends(get_db),
):
    """Enable a tenant — set status to 'active'."""
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == uuid.UUID(req.id)))
    ).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    tenant.status = "active"
    await session.flush()
    return ApiResponse(data=_tenant_to_out(tenant))


@router.post(
    "/disable",
    response_model=ApiResponse[TenantOut],
    summary="禁用租户",
    description="将租户状态设置为 disabled。",
    dependencies=[Depends(require_permission("tenant.update"))],
)
async def disable_tenant(
    req: IdRequest,
    session: AsyncSession = Depends(get_db),
):
    """Disable a tenant — set status to 'disabled'."""
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == uuid.UUID(req.id)))
    ).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    tenant.status = "disabled"
    await session.flush()
    return ApiResponse(data=_tenant_to_out(tenant))


# =============================================================================
# Member management (admin — operates on ANY tenant by tenant_id)
# =============================================================================
# These mirror tenant_self's member routes but take an explicit ``tenant_id``
# in the body so a super-admin / platform_ops can manage any tenant.


def _user_to_member_out(user: User) -> TenantMemberOut:
    """Project a User into the admin member shape.

    The admin UI exposes a single role per member (a Select bound to
    ``tenant_admin`` / ``tenant_developer`` / ``tenant_viewer`` codes), so we
    collapse the user's roles to one code.
    """
    role_code: str | None = None
    for r in user.roles:
        if r.code:
            role_code = r.code
            break
    if role_code is None and user.roles:
        role_code = user.roles[0].code or user.roles[0].name
    if role_code is None:
        role_code = "tenant_viewer"

    return TenantMemberOut(
        id=str(user.id),
        user_id=str(user.id),
        username=user.username,
        email=user.email,
        role=role_code,
        joined_at=user.created_at.isoformat(),
        status="active" if user.is_active else "disabled",
    )


async def _find_role_for_tenant(
    session: AsyncSession, tenant_id: uuid.UUID, role_code: str
) -> Role | None:
    """Find a role in the tenant matching the given code or name."""
    stmt = select(Role).where(
        Role.tenant_id == tenant_id,
        or_(Role.code == role_code, Role.name == role_code),
    )
    return (await session.execute(stmt)).scalars().first()


@router.post(
    "/members/list",
    response_model=ApiResponse[PaginatedResponse[TenantMemberOut]],
    summary="租户成员列表",
    description="分页查询指定租户的成员（超管视角）。",
    dependencies=[Depends(require_permission("user.manage"))],
)
async def list_tenant_members(
    req: TenantMembersListRequest,
    session: AsyncSession = Depends(get_db),
):
    """List members of the tenant identified by ``tenant_id`` in the body."""
    tenant_id = uuid.UUID(req.tenant_id)
    conditions = [User.tenant_id == tenant_id]

    total = (
        await session.execute(select(func.count()).select_from(User).where(*conditions))
    ).scalar() or 0

    offset = (req.page - 1) * req.page_size
    stmt = (
        select(User)
        .where(*conditions)
        .options(selectinload(User.roles))
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(req.page_size)
    )
    users = (await session.execute(stmt)).scalars().all()

    items = [_user_to_member_out(u) for u in users]
    return ApiResponse(
        data=PaginatedResponse(items=items, total=total, page=req.page, page_size=req.page_size)
    )


@router.post(
    "/members/create",
    response_model=ApiResponse[TenantMemberOut],
    summary="添加租户成员",
    description="向指定租户添加一个成员（按邮箱邀请）。实际邀请邮件发送未实现。",
    status_code=201,
    dependencies=[Depends(require_permission("user.manage"))],
)
async def create_tenant_member(
    req: TenantMemberInviteRequest,
    session: AsyncSession = Depends(get_db),
):
    """Add a member to the tenant identified by ``tenant_id``.

    The frontend only provides an email + role (no password/username), so we
    create the user with a random unusable password. A real invitation-email
    flow (token-based) is out of scope here — ``send_email`` is accepted but
    no mail is sent.
    """
    import bcrypt

    tenant_id = uuid.UUID(req.tenant_id)

    # Email is globally unique (functional unique index on lower(email))
    existing = (
        await session.execute(select(User).where(User.email == req.email))
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="邮箱已被占用")

    role = await _find_role_for_tenant(session, tenant_id, req.role)
    if not role:
        raise HTTPException(status_code=404, detail=f"租户中未找到角色: {req.role}")

    # No password from the admin UI — generate a random one (not returned).
    random_password = secrets.token_urlsafe(32)
    password_hash = bcrypt.hashpw(random_password.encode(), bcrypt.gensalt()).decode()

    # Use the email as the username (globally unique → avoids collisions).
    username = req.email if len(req.email) <= 64 else req.email[:64]

    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        username=username,
        email=req.email,
        password_hash=password_hash,
        is_active=True,
    )
    user.roles.append(role)
    session.add(user)
    await session.flush()

    return ApiResponse(data=_user_to_member_out(user))


@router.post(
    "/members/delete",
    response_model=ApiResponse,
    summary="移除租户成员",
    dependencies=[Depends(require_permission("user.manage"))],
)
async def delete_tenant_member(
    req: TenantMemberRemoveRequest,
    session: AsyncSession = Depends(get_db),
):
    """Remove a member from the tenant identified by ``tenant_id``."""
    tenant_id = uuid.UUID(req.tenant_id)
    user_id = uuid.UUID(req.user_id)

    user = await session.get(User, user_id)
    if not user or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="成员不存在")

    await session.delete(user)
    return ApiResponse(message="成员已移除")


@router.post(
    "/members/update-role",
    response_model=ApiResponse[TenantMemberOut],
    summary="更新租户成员角色",
    dependencies=[Depends(require_permission("user.manage"))],
)
async def update_tenant_member_role(
    req: TenantMemberRoleUpdateRequest,
    session: AsyncSession = Depends(get_db),
):
    """Replace a member's roles with a single role in the target tenant."""
    tenant_id = uuid.UUID(req.tenant_id)
    user_id = uuid.UUID(req.user_id)

    stmt = (
        select(User)
        .where(User.id == user_id, User.tenant_id == tenant_id)
        .options(selectinload(User.roles))
    )
    user = (await session.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="成员不存在")

    role = await _find_role_for_tenant(session, tenant_id, req.role)
    if not role:
        raise HTTPException(status_code=404, detail=f"租户中未找到角色: {req.role}")

    user.roles = [role]
    await session.flush()
    return ApiResponse(data=_user_to_member_out(user))