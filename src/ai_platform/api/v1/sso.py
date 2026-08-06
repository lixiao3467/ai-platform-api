"""SSO Provider management API — /api/v1/sso/providers.

Provides an extensible framework for configuring external identity providers:
- OAuth2 / OIDC (OpenID Connect)
- SAML 2.0 (planned)
- Feishu / DingTalk / WeCom (enterprise IM integration)

The provider abstraction allows adding new auth strategies without modifying
the core auth middleware.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.domain.models import SsoProvider
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class SsoProviderCreateRequest(BaseModel):
    """Request body for creating an SSO provider."""

    provider_type: str = Field(
        pattern="^(oidc|oauth2|saml|feishu|dingtalk|wecom)$",
        description="Provider type: oidc | oauth2 | saml | feishu | dingtalk | wecom",
    )
    name: str = Field(min_length=1, max_length=64, description="Unique internal name")
    display_name: str = Field(min_length=1, max_length=128, description="Display name shown to users")
    client_id: str = Field(min_length=1, max_length=256)
    client_secret: str = Field(min_length=1, max_length=512)
    issuer_url: str | None = Field(default=None, max_length=512, description="OIDC issuer URL or SAML IdP metadata URL")
    redirect_uri: str | None = Field(default=None, max_length=512)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "profile", "email"])
    extra_config: dict = Field(default_factory=dict, description="Provider-specific configuration")


class SsoProviderUpdateRequest(BaseModel):
    display_name: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    issuer_url: str | None = None
    redirect_uri: str | None = None
    scopes: list[str] | None = None
    extra_config: dict | None = None
    is_enabled: bool | None = None


class SsoProviderOut(BaseModel):
    id: str
    tenant_id: str
    provider_type: str
    name: str
    display_name: str
    client_id: str
    client_id_display: str  # Masked client_id for UI
    issuer_url: str | None
    redirect_uri: str | None
    scopes: list[str]
    is_enabled: bool
    extra_config: dict
    created_at: str
    updated_at: str


class SsoAuthorizeOut(BaseModel):
    """Response for SSO authorization initiation."""

    authorization_url: str
    state: str


# =============================================================================
# Helpers
# =============================================================================


def _mask_client_id(client_id: str) -> str:
    """Mask a client_id for display: show first 4 + last 4 chars."""
    if len(client_id) <= 8:
        return "****"
    return f"{client_id[:4]}...{client_id[-4:]}"


def _to_out(provider: SsoProvider) -> SsoProviderOut:
    return SsoProviderOut(
        id=str(provider.id),
        tenant_id=str(provider.tenant_id),
        provider_type=provider.provider_type,
        name=provider.name,
        display_name=provider.display_name,
        client_id=provider.client_id,
        client_id_display=_mask_client_id(provider.client_id),
        issuer_url=provider.issuer_url,
        redirect_uri=provider.redirect_uri,
        scopes=provider.scopes or [],
        is_enabled=provider.is_enabled,
        extra_config=provider.extra_config or {},
        created_at=provider.created_at.isoformat(),
        updated_at=provider.updated_at.isoformat(),
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "/providers",
    response_model=ApiResponse[list[SsoProviderOut]],
    summary="获取 SSO 提供者列表",
    description="返回当前租户所有已配置的 SSO 身份提供者。client_secret 不会被返回。",
    dependencies=[Depends(require_permission("system.config"))],
)
async def list_sso_providers(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List SSO providers for the current tenant."""
    stmt = (
        select(SsoProvider)
        .where(SsoProvider.tenant_id == ctx.tenant_id)
        .order_by(SsoProvider.created_at)
    )
    result = await session.execute(stmt)
    providers = result.scalars().all()
    return ApiResponse(data=[_to_out(p) for p in providers])


@router.post(
    "/providers",
    response_model=ApiResponse[SsoProviderOut],
    summary="创建 SSO 提供者",
    description="注册一个新的 SSO 身份提供者。client_secret 会加密存储。",
    dependencies=[Depends(require_permission("system.config"))],
    responses={
        200: {"description": "创建成功"},
        409: {"description": "提供者名称已存在"},
    },
)
async def create_sso_provider(
    req: SsoProviderCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Create a new SSO provider."""
    # Check duplicate name within tenant
    existing = await session.execute(
        select(SsoProvider).where(
            SsoProvider.tenant_id == ctx.tenant_id,
            SsoProvider.name == req.name,
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"SSO 提供者名称 '{req.name}' 已存在")

    # Encrypt client_secret before storing
    from ai_platform.infra.secrets.crypto import encrypt_secret
    encrypted_secret = encrypt_secret(req.client_secret)

    provider = SsoProvider(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        provider_type=req.provider_type,
        name=req.name,
        display_name=req.display_name,
        client_id=req.client_id,
        client_secret_encrypted=encrypted_secret,
        issuer_url=req.issuer_url,
        redirect_uri=req.redirect_uri,
        scopes=req.scopes,
        extra_config=req.extra_config,
        is_enabled=True,
    )
    session.add(provider)
    await session.flush()

    return ApiResponse(data=_to_out(provider))


@router.put(
    "/providers/{provider_id}",
    response_model=ApiResponse[SsoProviderOut],
    summary="更新 SSO 提供者",
    dependencies=[Depends(require_permission("system.config"))],
)
async def update_sso_provider(
    provider_id: uuid.UUID,
    req: SsoProviderUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Update an SSO provider."""
    stmt = select(SsoProvider).where(
        SsoProvider.id == provider_id,
        SsoProvider.tenant_id == ctx.tenant_id,
    )
    result = await session.execute(stmt)
    provider = result.scalars().first()
    if not provider:
        raise HTTPException(status_code=404, detail="SSO 提供者不存在")

    if req.display_name is not None:
        provider.display_name = req.display_name
    if req.client_id is not None:
        provider.client_id = req.client_id
    if req.client_secret is not None:
        from ai_platform.infra.secrets.crypto import encrypt_secret
        provider.client_secret_encrypted = encrypt_secret(req.client_secret)
    if req.issuer_url is not None:
        provider.issuer_url = req.issuer_url
    if req.redirect_uri is not None:
        provider.redirect_uri = req.redirect_uri
    if req.scopes is not None:
        provider.scopes = req.scopes
    if req.extra_config is not None:
        provider.extra_config = req.extra_config
    if req.is_enabled is not None:
        provider.is_enabled = req.is_enabled

    await session.flush()
    return ApiResponse(data=_to_out(provider))


@router.delete(
    "/providers/{provider_id}",
    response_model=ApiResponse,
    summary="删除 SSO 提供者",
    dependencies=[Depends(require_permission("system.config"))],
)
async def delete_sso_provider(
    provider_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Delete an SSO provider."""
    stmt = select(SsoProvider).where(
        SsoProvider.id == provider_id,
        SsoProvider.tenant_id == ctx.tenant_id,
    )
    result = await session.execute(stmt)
    provider = result.scalars().first()
    if not provider:
        raise HTTPException(status_code=404, detail="SSO 提供者不存在")

    await session.delete(provider)
    return ApiResponse(message="SSO 提供者已删除")


@router.get(
    "/providers/{provider_id}/authorize",
    response_model=ApiResponse[SsoAuthorizeOut],
    summary="发起 SSO 授权",
    description="生成第三方身份提供者的授权 URL。前端重定向用户至此 URL 完成登录。",
    responses={
        200: {"description": "返回授权 URL"},
        404: {"description": "提供者不存在"},
        400: {"description": "提供者未启用或类型不支持"},
    },
)
async def initiate_sso_authorize(
    provider_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Initiate SSO authorization — returns the IdP redirect URL."""
    stmt = select(SsoProvider).where(
        SsoProvider.id == provider_id,
        SsoProvider.tenant_id == ctx.tenant_id,
    )
    result = await session.execute(stmt)
    provider = result.scalars().first()

    if not provider:
        raise HTTPException(status_code=404, detail="SSO 提供者不存在")
    if not provider.is_enabled:
        raise HTTPException(status_code=400, detail="SSO 提供者未启用")

    # Build authorization URL based on provider type
    state = str(uuid.uuid4())

    if provider.provider_type in ("oidc", "oauth2"):
        # Discover authorization endpoint from issuer
        issuer = provider.issuer_url or ""
        if not issuer:
            raise HTTPException(status_code=400, detail="缺少 issuer_url 配置")

        # OIDC well-known discovery
        authorization_endpoint = f"{issuer.rstrip('/')}/authorize"
        if provider.extra_config.get("authorization_endpoint"):
            authorization_endpoint = provider.extra_config["authorization_endpoint"]

        params = {
            "client_id": provider.client_id,
            "response_type": "code",
            "redirect_uri": provider.redirect_uri or "",
            "scope": " ".join(provider.scopes or ["openid", "profile", "email"]),
            "state": state,
        }

        from urllib.parse import urlencode
        auth_url = f"{authorization_endpoint}?{urlencode(params)}"

        # Store state in Redis for CSRF validation (TTL: 10 min)
        from ai_platform.infra.cache.redis_client import get_redis
        redis = await get_redis()
        await redis.setex(
            f"aip:sso_state:{state}",
            600,
            str(provider.id),
        )

        return ApiResponse(data=SsoAuthorizeOut(
            authorization_url=auth_url,
            state=state,
        ))

    elif provider.provider_type == "feishu":
        # Feishu OAuth2
        auth_url = (
            f"https://open.feishu.cn/open-apis/authen/v1/authorize"
            f"?app_id={provider.client_id}"
            f"&redirect_uri={provider.redirect_uri or ''}"
            f"&state={state}"
        )
        return ApiResponse(data=SsoAuthorizeOut(authorization_url=auth_url, state=state))

    elif provider.provider_type == "dingtalk":
        # DingTalk OAuth2
        from urllib.parse import urlencode
        params = {
            "client_id": provider.client_id,
            "redirect_uri": provider.redirect_uri or "",
            "response_type": "code",
            "scope": "openid",
            "state": state,
            "prompt": "consent",
        }
        auth_url = f"https://login.dingtalk.com/oauth2/auth?{urlencode(params)}"
        return ApiResponse(data=SsoAuthorizeOut(authorization_url=auth_url, state=state))

    elif provider.provider_type == "wecom":
        # WeCom (企业微信) OAuth2
        from urllib.parse import urlencode
        params = {
            "appid": provider.client_id,
            "redirect_uri": provider.redirect_uri or "",
            "response_type": "code",
            "scope": "snsapi_privateinfo",
            "state": state,
        }
        auth_url = f"https://open.work.weixin.qq.com/wwopen/sso/3rd_qrConnect?{urlencode(params)}"
        return ApiResponse(data=SsoAuthorizeOut(authorization_url=auth_url, state=state))

    elif provider.provider_type == "saml":
        # SAML — placeholder
        raise HTTPException(status_code=501, detail="SAML 授权流程尚未实现")

    raise HTTPException(status_code=400, detail=f"不支持的提供者类型: {provider.provider_type}")


@router.get(
    "/callback/{provider_name}",
    summary="SSO 回调",
    description="处理身份提供者的 OAuth 回调。验证 state，交换 code 获取用户信息，创建或关联本地用户。",
    responses={
        302: {"description": "重定向到前端（附带 JWT token）"},
        400: {"description": "无效的回调参数"},
    },
)
async def sso_callback(
    provider_name: str,
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_db),
):
    """Handle OAuth/OIDC callback from identity provider.

    This is the callback endpoint that the IdP redirects to after user authorization.
    It validates the state parameter, exchanges the authorization code for tokens,
    fetches user info, and creates/links a local user account.

    TODO: Implement full callback flow:
    1. Validate state against Redis
    2. Look up provider by name
    3. Exchange code for access_token + id_token
    4. Fetch user info from IdP
    5. Find or create local User record
    6. Issue JWT tokens
    7. Redirect to frontend with tokens
    """
    from fastapi.responses import RedirectResponse

    # Validate state
    from ai_platform.infra.cache.redis_client import get_redis
    redis = await get_redis()
    provider_id = await redis.get(f"aip:sso_state:{state}")
    if not provider_id:
        return RedirectResponse(url="/login?error=invalid_state")

    # Clean up state
    await redis.delete(f"aip:sso_state:{state}")

    # TODO: Full implementation requires provider-specific token exchange
    # For now, return a placeholder error
    raise HTTPException(
        status_code=501,
        detail="SSO 回调处理尚未完整实现。需要完成 code → token → user info 的交换流程。",
    )
