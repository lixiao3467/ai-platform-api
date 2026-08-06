"""Users & Roles API — /api/v1/users/* and /api/v1/roles/*."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
import bcrypt
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_platform.api.middleware.auth import (
    RequestContext,
    get_request_context,
    create_jwt_token,
    create_refresh_token,
    decode_jwt_token,
    revoke_refresh_token,
    is_refresh_token_revoked,
)
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.domain.models import Permission, Role, User, role_permissions, user_roles
from ai_platform.infra.database.connection import get_db


def _parse_uuid_list(ids: list[str]) -> list[uuid.UUID]:
    """Parse a list of string IDs to UUIDs, raising HTTP 422 on invalid format."""
    result: list[uuid.UUID] = []
    for raw in ids:
        try:
            result.append(uuid.UUID(raw))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail=f"Invalid UUID format: {raw}")
    return result

users_router = APIRouter()
roles_router = APIRouter()
auth_router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    refresh_token: str
    expires_in: int = 0  # seconds until access token expires
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    token: str
    refresh_token: str
    expires_in: int = 0


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    email: str = Field(max_length=128)
    password: str = Field(min_length=6)
    display_name: str | None = None
    phone: str | None = None
    role_ids: list[str] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    display_name: str | None = None
    phone: str | None = None
    email: str | None = None
    is_active: bool | None = None
    role_ids: list[str] | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6)


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    display_name: str | None
    phone: str | None
    is_active: bool
    is_superadmin: bool
    roles: list[dict]
    last_login_at: str | None
    created_at: str


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    permission_ids: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    permission_ids: list[str] | None = None


class RoleOut(BaseModel):
    id: str
    name: str
    description: str | None
    is_system: bool
    permissions: list[dict]
    user_count: int = 0
    created_at: str


class PermissionOut(BaseModel):
    id: str
    resource: str
    action: str
    description: str | None


# =============================================================================
# Auth — Login
# =============================================================================


@auth_router.post(
    "/login",
    response_model=ApiResponse[LoginResponse],
    summary="用户登录",
    description="使用用户名和密码登录，返回 access token 和 refresh token。access token 有效期较短（默认30分钟），refresh token 有效期较长（默认7天）。",
    responses={
        200: {"description": "登录成功"},
        401: {"description": "用户名或密码错误"},
        403: {"description": "账号已被禁用"},
    },
)
async def login(
    req: LoginRequest,
    session: AsyncSession = Depends(get_db),
):
    """用户登录 — 返回 access token + refresh token。"""
    stmt = select(User).where(User.username == req.username).options(
        selectinload(User.roles).selectinload(Role.permissions)
    )
    result = await session.execute(stmt)
    user = result.scalars().first()

    if not user or not bcrypt.checkpw(req.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    # Update last login
    user.last_login_at = datetime.now(tz=timezone.utc)
    await session.flush()

    from ai_platform.config import get_settings
    settings = get_settings()

    access_token = create_jwt_token(str(user.tenant_id), str(user.id))
    refresh_token = create_refresh_token(str(user.tenant_id), str(user.id))

    return ApiResponse(data=LoginResponse(
        token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_expire_minutes * 60,
        user={
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "display_name": user.display_name,
            "tenant_id": str(user.tenant_id),
            "roles": [
                {"id": str(r.id), "name": r.name, "code": r.code or r.name}
                for r in user.roles
            ],
            "permissions": ["*"] if user.is_superadmin else list({
                f"{p.resource}.{p.action}"
                for r in user.roles
                for p in r.permissions
            }),
            "is_superadmin": user.is_superadmin,
        },
    ))


@auth_router.post(
    "/refresh",
    response_model=ApiResponse[RefreshResponse],
    summary="刷新访问令牌",
    description="使用 refresh token 获取新的 access token 和 refresh token（rotation）。旧的 refresh token 立即失效。",
    responses={
        200: {"description": "刷新成功，返回新 token 对"},
        401: {"description": "refresh token 无效、已过期或已被撤销"},
    },
)
async def refresh_token(
    req: RefreshRequest,
    session: AsyncSession = Depends(get_db),
):
    """刷新令牌 — 用 refresh token 换取新的 access + refresh token pair (rotation)。"""
    from ai_platform.config import get_settings
    settings = get_settings()

    # 1. Decode and validate the refresh token
    try:
        from jose import jwt, JWTError
        payload = jwt.decode(
            req.refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": True, "verify_aud": False},
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="refresh token 无效或已过期")

    # 2. Must be a refresh-type token
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="token 类型错误")

    # 3. Check revocation list
    jti = payload.get("jti", "")
    if await is_refresh_token_revoked(jti):
        raise HTTPException(status_code=401, detail="refresh token 已被撤销")

    # 4. Verify user still exists and is active
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    if not user_id or not tenant_id:
        raise HTTPException(status_code=401, detail="token 数据不完整")

    stmt = select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    result = await session.execute(stmt)
    user = result.scalars().first()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已被禁用")

    # 5. Revoke old refresh token (rotation)
    await revoke_refresh_token(jti)

    # 6. Issue new token pair
    new_access = create_jwt_token(str(user.tenant_id), str(user.id))
    new_refresh = create_refresh_token(str(user.tenant_id), str(user.id))

    return ApiResponse(data=RefreshResponse(
        token=new_access,
        refresh_token=new_refresh,
        expires_in=settings.jwt_expire_minutes * 60,
    ))


@auth_router.post(
    "/logout",
    response_model=ApiResponse,
    summary="登出",
    description="撤销当前 refresh token，使其无法再用于刷新。",
)
async def logout(
    req: RefreshRequest,
):
    """登出 — 撤销 refresh token。"""
    try:
        from jose import jwt, JWTError
        from ai_platform.config import get_settings
        settings = get_settings()

        payload = jwt.decode(
            req.refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False, "verify_aud": False},
        )
        jti = payload.get("jti", "")
        if jti:
            await revoke_refresh_token(jti)
    except Exception:
        pass  # Best effort — even if token is invalid, we return success

    return ApiResponse(message="已登出")


# =============================================================================
# Users CRUD
# =============================================================================


@users_router.get("/", response_model=ApiResponse[PaginatedResponse[UserOut]], dependencies=[Depends(require_permission("user.manage"))])
async def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """用户列表。"""
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

    return ApiResponse(data=PaginatedResponse(
        items=[_user_to_out(u) for u in users],
        total=total, page=page, page_size=page_size,
    ))


@users_router.post("/", response_model=ApiResponse[UserOut], dependencies=[Depends(require_permission("user.manage"))])
async def create_user(
    req: UserCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """创建用户。"""
    # Check duplicate email OR username within tenant
    existing = await session.execute(
        select(User).where(
            User.tenant_id == ctx.tenant_id,
            (User.email == req.email) | (User.username == req.username),
        )
    )
    duplicate = existing.scalars().first()
    if duplicate:
        field_name = "邮箱" if duplicate.email == req.email else "用户名"
        raise HTTPException(status_code=409, detail=f"{field_name}已被注册")

    user = User(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        username=req.username,
        email=req.email,
        password_hash=bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode(),
        display_name=req.display_name,
        phone=req.phone,
    )

    # Assign roles
    if req.role_ids:
        role_uuids = _parse_uuid_list(req.role_ids)
        roles = await session.execute(select(Role).where(Role.id.in_(role_uuids)))
        user.roles = list(roles.scalars().all())

    session.add(user)
    await session.flush()

    # Reload with roles
    stmt = select(User).where(User.id == user.id).options(selectinload(User.roles))
    user = (await session.execute(stmt)).scalars().first()

    return ApiResponse(data=_user_to_out(user))


@users_router.put("/{user_id}", response_model=ApiResponse[UserOut], dependencies=[Depends(require_permission("user.update"))])
async def update_user(
    user_id: uuid.UUID,
    req: UserUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """更新用户信息。"""
    stmt = select(User).where(User.id == user_id, User.tenant_id == ctx.tenant_id).options(selectinload(User.roles))
    user = (await session.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if req.display_name is not None:
        user.display_name = req.display_name
    if req.phone is not None:
        user.phone = req.phone
    if req.email is not None:
        user.email = req.email
    if req.is_active is not None:
        user.is_active = req.is_active

    if req.role_ids is not None:
        role_uuids = _parse_uuid_list(req.role_ids)
        roles = await session.execute(select(Role).where(Role.id.in_(role_uuids)))
        user.roles = list(roles.scalars().all())

    await session.flush()

    stmt = select(User).where(User.id == user.id).options(selectinload(User.roles))
    user = (await session.execute(stmt)).scalars().first()

    return ApiResponse(data=_user_to_out(user))


@users_router.delete("/{user_id}", response_model=ApiResponse, dependencies=[Depends(require_permission("user.delete"))])
async def delete_user(
    user_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """删除用户。"""
    stmt = select(User).where(User.id == user_id, User.tenant_id == ctx.tenant_id)
    user = (await session.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.is_superadmin:
        raise HTTPException(status_code=403, detail="不能删除超级管理员")

    await session.delete(user)
    return ApiResponse(message="用户已删除")


@users_router.post("/{user_id}/reset-password", response_model=ApiResponse, dependencies=[Depends(require_permission("user.update"))])
async def reset_password(
    user_id: uuid.UUID,
    req: ResetPasswordRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """重置用户密码。"""
    stmt = select(User).where(User.id == user_id, User.tenant_id == ctx.tenant_id)
    user = (await session.execute(stmt)).scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.password_hash = bcrypt.hashpw(req.new_password.encode(), bcrypt.gensalt()).decode()
    await session.flush()
    return ApiResponse(message="密码已重置")


# =============================================================================
# Roles CRUD
# =============================================================================


@roles_router.get("/", response_model=ApiResponse[list[RoleOut]], dependencies=[Depends(require_permission("user.manage"))])
async def list_roles(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """角色列表。"""
    stmt = (
        select(Role)
        .where(Role.tenant_id == ctx.tenant_id)
        .options(selectinload(Role.permissions))
        .order_by(Role.created_at)
    )
    result = await session.execute(stmt)
    roles = result.scalars().all()

    # Count users per role
    items = []
    for role in roles:
        user_count = (await session.execute(
            select(func.count()).select_from(user_roles).where(user_roles.c.role_id == role.id)
        )).scalar() or 0

        items.append(RoleOut(
            id=str(role.id), name=role.name, description=role.description,
            is_system=role.is_system,
            permissions=[{"id": str(p.id), "resource": p.resource, "action": p.action} for p in role.permissions],
            user_count=user_count,
            created_at=role.created_at.isoformat(),
        ))

    return ApiResponse(data=items)


@roles_router.post("/", response_model=ApiResponse[RoleOut], dependencies=[Depends(require_permission("user.manage"))])
async def create_role(
    req: RoleCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """创建角色。"""
    role = Role(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        name=req.name,
        description=req.description,
    )

    if req.permission_ids:
        perm_uuids = _parse_uuid_list(req.permission_ids)
        perms = await session.execute(select(Permission).where(Permission.id.in_(perm_uuids)))
        role.permissions = list(perms.scalars().all())

    session.add(role)
    await session.flush()

    stmt = select(Role).where(Role.id == role.id).options(selectinload(Role.permissions))
    role = (await session.execute(stmt)).scalars().first()

    return ApiResponse(data=RoleOut(
        id=str(role.id), name=role.name, description=role.description,
        is_system=False,
        permissions=[{"id": str(p.id), "resource": p.resource, "action": p.action} for p in role.permissions],
        created_at=role.created_at.isoformat(),
    ))


@roles_router.put("/{role_id}", response_model=ApiResponse[RoleOut], dependencies=[Depends(require_permission("user.manage"))])
async def update_role(
    role_id: uuid.UUID,
    req: RoleUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """更新角色。"""
    stmt = select(Role).where(Role.id == role_id, Role.tenant_id == ctx.tenant_id).options(selectinload(Role.permissions))
    role = (await session.execute(stmt)).scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    if req.name is not None:
        role.name = req.name
    if req.description is not None:
        role.description = req.description

    if req.permission_ids is not None:
        perm_uuids = _parse_uuid_list(req.permission_ids)
        perms = await session.execute(select(Permission).where(Permission.id.in_(perm_uuids)))
        role.permissions = list(perms.scalars().all())

    await session.flush()
    return ApiResponse(message="角色已更新")


@roles_router.delete("/{role_id}", response_model=ApiResponse, dependencies=[Depends(require_permission("user.manage"))])
async def delete_role(
    role_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """删除角色（系统角色不可删除）。"""
    stmt = select(Role).where(Role.id == role_id, Role.tenant_id == ctx.tenant_id)
    role = (await session.execute(stmt)).scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.is_system:
        raise HTTPException(status_code=403, detail="系统角色不可删除")

    await session.delete(role)
    return ApiResponse(message="角色已删除")


# =============================================================================
# Permissions
# =============================================================================


@roles_router.get("/permissions", response_model=ApiResponse[list[PermissionOut]], dependencies=[Depends(require_permission("user.manage"))])
async def list_permissions(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """获取所有可用权限列表。"""
    result = await session.execute(select(Permission).order_by(Permission.resource, Permission.action))
    perms = result.scalars().all()
    return ApiResponse(data=[
        PermissionOut(id=str(p.id), resource=p.resource, action=p.action, description=p.description)
        for p in perms
    ])


# =============================================================================
# Helpers
# =============================================================================


def _user_to_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id),
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        phone=user.phone,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        roles=[{"id": str(r.id), "name": r.name} for r in (user.roles or [])],
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
        created_at=user.created_at.isoformat(),
    )
