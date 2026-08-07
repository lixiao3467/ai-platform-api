"""Auth API — /api/v1/auth/* (login, refresh, logout, switch-role, me)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
import bcrypt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_platform.api.middleware.auth import (
    RequestContext,
    get_request_context,
    create_jwt_token,
    create_refresh_token,
    revoke_refresh_token,
    is_refresh_token_revoked,
)
from ai_platform.domain.models import Role, User
from ai_platform.infra.cache.redis_client import get_redis
from ai_platform.infra.database.connection import get_db
from ai_platform.api.schemas.common import ApiResponse


auth_router = APIRouter()


# =============================================================================
# Active-role Redis helpers
# =============================================================================
# Instead of re-issuing JWTs on role switch we persist the user's currently
# selected role in Redis. The auth middleware reads this key and filters
# permissions accordingly. TTL is tied to the refresh-token lifetime so the
# selection survives across access-token rotations but expires naturally.


_ACTIVE_ROLE_TTL_S = 30 * 86400  # 30 days
# Hard cap on any single Redis op in the auth hot path (defense-in-depth
# on top of socket_timeout=2 in redis_client).
_REDIS_OP_TIMEOUT_S = 2.0


async def _get_active_role_code(user_id: str) -> str | None:
    """Read the user's active role code from Redis (None = not set)."""
    try:
        redis = await get_redis()
        return await asyncio.wait_for(
            redis.get(f"aip:user:{user_id}:active_role"),
            timeout=_REDIS_OP_TIMEOUT_S,
        )
    except Exception:
        return None


async def _set_active_role_code(user_id: str, role_code: str) -> None:
    """Persist the user's active role code in Redis."""
    redis = await get_redis()
    await asyncio.wait_for(
        redis.setex(f"aip:user:{user_id}:active_role", _ACTIVE_ROLE_TTL_S, role_code),
        timeout=_REDIS_OP_TIMEOUT_S,
    )


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


class SwitchRoleRequest(BaseModel):
    role_id: str | None = None
    role_code: str | None = None


class SwitchRoleResponse(BaseModel):
    active_role: str
    permissions: list[str]


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

    # Determine active_role: read from Redis, fall back to first role
    roles_list = [
        {"id": str(r.id), "name": r.name, "code": r.code or r.name}
        for r in user.roles
    ]
    stored_active = await _get_active_role_code(str(user.id))
    valid_codes = {r["code"] for r in roles_list}
    active_role = stored_active if stored_active in valid_codes else (
        next(iter(valid_codes)) if valid_codes else None
    )

    all_permissions = ["*"] if user.is_superadmin else list({
        f"{p.resource}.{p.action}"
        for r in user.roles
        for p in r.permissions
    })

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
            "roles": roles_list,
            "permissions": all_permissions,
            "is_superadmin": user.is_superadmin,
            "active_role": active_role,
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
# Auth — Switch Role / Current User
# =============================================================================


@auth_router.post(
    "/switch-role",
    response_model=ApiResponse[SwitchRoleResponse],
    summary="切换当前活跃角色",
    description="在多角色用户场景下，选择一个角色作为当前活跃角色。切换后，后续请求的权限检查将仅基于该角色拥有的权限。"
                "请求体中提供 `role_id` 或 `role_code` 之一即可。",
    responses={
        200: {"description": "切换成功"},
        400: {"description": "请求参数缺失或格式错误"},
        403: {"description": "用户不拥有该角色（越权）"},
        404: {"description": "目标角色不存在"},
    },
)
async def switch_role(
    req: SwitchRoleRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """切换当前活跃角色 — 用户只能切换到自己拥有的角色。"""
    if not req.role_id and not req.role_code:
        raise HTTPException(status_code=400, detail="请提供 role_id 或 role_code")

    if not ctx.user_id:
        raise HTTPException(status_code=401, detail="无法识别当前用户")

    # Load user with roles+permissions
    stmt = (
        select(User)
        .where(User.id == ctx.user_id, User.tenant_id == ctx.tenant_id)
        .options(selectinload(User.roles).selectinload(Role.permissions))
    )
    result = await session.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    # Find the target role among user's assigned roles
    target_role: Role | None = None
    for role in user.roles:
        if req.role_id and str(role.id) == req.role_id:
            target_role = role
            break
        if req.role_code and (role.code or role.name) == req.role_code:
            target_role = role
            break

    if not target_role:
        raise HTTPException(status_code=403, detail="您不拥有该角色，无法切换")

    role_code = target_role.code or target_role.name

    # Persist the active role selection in Redis
    await _set_active_role_code(str(user.id), role_code)

    # Calculate permissions for the selected role
    if user.is_superadmin:
        permissions = ["*"]
    else:
        permissions = list({
            f"{p.resource}.{p.action}"
            for p in target_role.permissions
        })

    # Invalidate permission caches so the next request picks up the change
    from ai_platform.api.middleware.auth import _perm_cache
    perm_cache_key_full = f"aip:user_perms:{user.id}"
    _perm_cache.delete(perm_cache_key_full)
    # Also delete any role-specific L1 entries for this user
    for key in list(_perm_cache._store.keys()):
        if key.startswith(f"{perm_cache_key_full}:"):
            _perm_cache.delete(key)
    try:
        redis = await get_redis()
        # Delete base + role-suffixed keys via SCAN (best effort)
        cursor = 0
        while True:
            cursor, keys = await asyncio.wait_for(
                redis.scan(cursor=cursor, match=f"{perm_cache_key_full}*", count=100),
                timeout=_REDIS_OP_TIMEOUT_S,
            )
            if keys:
                await asyncio.wait_for(redis.delete(*keys), timeout=_REDIS_OP_TIMEOUT_S)
            if cursor == 0:
                break
    except Exception:
        pass  # Best effort

    return ApiResponse(data=SwitchRoleResponse(
        active_role=role_code,
        permissions=permissions,
    ))


@auth_router.post(
    "/me",
    response_model=ApiResponse[dict],
    summary="获取当前用户信息",
    description="返回当前认证用户的基本信息、角色列表、活跃角色及权限列表。",
)
async def get_me(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """获取当前用户信息 — 包含 active_role 和过滤后的 permissions。"""
    if not ctx.user_id:
        raise HTTPException(status_code=401, detail="无法识别当前用户")

    stmt = (
        select(User)
        .where(User.id == ctx.user_id, User.tenant_id == ctx.tenant_id)
        .options(selectinload(User.roles))
    )
    result = await session.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    roles_list = [
        {"id": str(r.id), "name": r.name, "code": r.code or r.name}
        for r in user.roles
    ]

    # Active role: prefer the value already resolved by the middleware
    active_role = ctx.active_role
    if not active_role:
        stored_active = await _get_active_role_code(str(user.id))
        valid_codes = {r["code"] for r in roles_list}
        active_role = stored_active if stored_active in valid_codes else (
            next((r["code"] for r in roles_list), None)
        )

    # Permissions: the middleware has already filtered by active_role, so we
    # can just use ctx.permissions. Superadmins always get ["*"].
    permissions: list[str] = ["*"] if user.is_superadmin else list(ctx.permissions)

    return ApiResponse(data={
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "display_name": user.display_name,
        "tenant_id": str(user.tenant_id),
        "is_superadmin": user.is_superadmin,
        "roles": roles_list,
        "active_role": active_role,
        "permissions": permissions,
    })
