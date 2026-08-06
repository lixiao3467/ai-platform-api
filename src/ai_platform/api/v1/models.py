"""Models & Providers API — /api/v1/models/*."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse
from ai_platform.infra.database.connection import get_db
from ai_platform.services.model_resolver import (
    ModelResolverService,
    VALID_PURPOSES,
)
from ai_platform.services.provider_service import ProviderService

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================

# B-10: whitelist of known provider identifiers (runtime-validated, flexible).
ALLOWED_PROVIDERS: set[str] = {
    "openai",
    "anthropic",
    "qwen",
    "deepseek",
    "ollama",
    "vllm",
    "azure",
    "google",
    "cohere",
    "mistral",
    "xai",
    "gemini",
}


class ModelConfigInput(BaseModel):
    """B-11: typed schema for a model entry inside a provider config.

    Replaces the previous ``dict[str, Any]`` so the API rejects malformed
    payloads with a clear validation error instead of persisting garbage.
    """

    name: str = Field(description="Model identifier, e.g. 'gpt-4o'")
    context_length: int | None = Field(
        default=None, description="Max context tokens"
    )
    purposes: list[str] = Field(
        default_factory=list,
        description="Supported purposes: llm, vision, embedding, etc.",
    )
    capabilities: list[str] = Field(
        default_factory=list, description="Additional capabilities"
    )
    enabled: bool = Field(
        default=True, description="Whether this model is enabled"
    )
    input_price: float | None = Field(
        default=None, description="Input price per 1M tokens (USD)"
    )
    output_price: float | None = Field(
        default=None, description="Output price per 1M tokens (USD)"
    )


class ProviderCreateRequest(BaseModel):
    provider_name: str = Field(
        description="Provider identifier: openai / anthropic / qwen / deepseek / ollama / vllm"
    )
    display_name: str | None = Field(default=None, description="Human-readable name")
    api_base_url: str | None = Field(
        default=None, description="Custom API base URL (for private deployments)"
    )
    api_key: str | None = Field(
        default=None, description="API key — will be encrypted before storage"
    )
    models: list[ModelConfigInput] = Field(
        default_factory=list,
        description=(
            'Model list, e.g. [{"name": "gpt-4o", "context_length": 128000, '
            '"purposes": ["llm", "vision"], "enabled": true}]'
        ),
    )
    priority: int = Field(default=0, description="Higher = preferred in routing")

    @field_validator("provider_name")
    @classmethod
    def validate_provider_name(cls, v: str) -> str:
        normalised = v.lower().strip()
        if normalised not in ALLOWED_PROVIDERS:
            raise ValueError(
                f"Unknown provider '{v}'. Allowed: {sorted(ALLOWED_PROVIDERS)}"
            )
        return normalised


class ProviderUpdateKeyRequest(BaseModel):
    api_key: str = Field(description="New API key — will be encrypted before storage")


class ProviderUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, description="Human-readable name")
    api_base_url: str | None = Field(
        default=None, description="Custom API base URL (for private deployments)"
    )
    api_key: str | None = Field(
        default=None,
        description="New API key — will be encrypted before storage. "
        "Supplying a new key (or changing api_base_url) auto-disables the "
        "provider until connectivity is re-verified.",
    )
    models: list[ModelConfigInput] | None = Field(
        default=None,
        description=(
            'Model list, e.g. [{"name": "gpt-4o", "context_length": 128000, '
            '"purposes": ["llm", "vision"], "enabled": true}]'
        ),
    )
    priority: int | None = Field(default=None, description="Higher = preferred in routing")


class ProviderOut(BaseModel):
    id: str
    provider_name: str
    display_name: str | None
    api_base_url: str | None
    api_key_display: str | None = Field(description="Masked key (e.g. sk-a...z789)")
    models: list[dict[str, Any]]
    is_enabled: bool
    priority: int
    created_at: str | None
    needs_retest: bool = Field(
        default=False,
        description="True when api_base_url or api_key changed — provider was auto-disabled and needs a connectivity test before re-enabling.",
    )
    last_test_at: str | None = Field(
        default=None, description="ISO timestamp of last connectivity test (UTC)."
    )
    last_test_success: bool | None = Field(
        default=None, description="Outcome of last connectivity test."
    )
    last_test_latency_ms: int | None = Field(
        default=None, description="Latency of last connectivity test in ms."
    )


class ProviderTestResultOut(BaseModel):
    success: bool
    latency_ms: int
    model: str
    message: str


class ModelToggleRequest(BaseModel):
    """B-07: typed body for per-model enable/disable endpoint."""

    enabled: bool = Field(description="True to enable the model, False to disable.")


# =============================================================================
# Provider CRUD — 密钥通过后台管理，加密存储
# =============================================================================


@router.post("/providers", response_model=ApiResponse[ProviderOut], dependencies=[Depends(require_permission("model.manage"))])
async def create_provider(
    req: ProviderCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """
    添加模型提供商（API Key 加密存储）。

    密钥不会出现在日志、响应或环境变量中。
    仅通过此 API 或管理后台进行增删改。
    """
    svc = ProviderService(session)
    provider = await svc.create_provider(
        ctx.tenant_id,
        provider_name=req.provider_name,
        display_name=req.display_name,
        api_base_url=req.api_base_url,
        api_key=req.api_key,
        models=[m.model_dump(exclude_none=False) for m in req.models],
        priority=req.priority,
    )

    # Return with masked key
    providers = await svc.list_providers(ctx.tenant_id)
    provider_data = next(
        (p for p in providers if p["id"] == str(provider.id)), None
    )

    return ApiResponse(data=ProviderOut(**provider_data))


@router.get("/providers", response_model=ApiResponse[list[ProviderOut]], dependencies=[Depends(require_permission("model.read"))])
async def list_providers(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """列出当前租户的所有模型提供商（密钥已脱敏）。"""
    svc = ProviderService(session)
    providers = await svc.list_providers(ctx.tenant_id)
    return ApiResponse(data=[ProviderOut(**p) for p in providers])


@router.put("/providers/{provider_id}/key", response_model=ApiResponse, dependencies=[Depends(require_permission("model.manage"))])
async def update_provider_key(
    provider_id: uuid.UUID,
    req: ProviderUpdateKeyRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """更新提供商的 API Key（重新加密存储）。"""
    svc = ProviderService(session)
    try:
        await svc.update_api_key(provider_id, req.api_key)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ApiResponse(message="API key updated (encrypted)")


@router.put("/providers/{provider_id}", response_model=ApiResponse[ProviderOut], dependencies=[Depends(require_permission("model.manage"))])
async def update_provider(
    provider_id: uuid.UUID,
    req: ProviderUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """更新提供商配置（显示名称、API 地址、API Key、模型列表、优先级）。

    当 ``api_base_url`` 或 ``api_key`` 发生变更时，提供商会被自动禁用
    （``is_enabled=False``），响应中 ``needs_retest=true`` 提示前端需要
    先执行连通性测试再重新启用。
    """
    svc = ProviderService(session)
    try:
        _provider, needs_retest = await svc.update_provider(
            provider_id,
            display_name=req.display_name,
            api_base_url=req.api_base_url,
            api_key=req.api_key,
            models=(
                [m.model_dump(exclude_none=False) for m in req.models]
                if req.models is not None
                else None
            ),
            priority=req.priority,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # Return updated provider with masked key
    providers = await svc.list_providers(ctx.tenant_id)
    provider_data = next((p for p in providers if p["id"] == str(provider_id)), None)
    if provider_data is not None:
        provider_data["needs_retest"] = needs_retest
    return ApiResponse(data=ProviderOut(**provider_data))


@router.post(
    "/providers/{provider_id}/test",
    response_model=ApiResponse[ProviderTestResultOut],
    dependencies=[Depends(require_permission("model.manage"))],
)
async def test_provider(
    provider_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """测试提供商连通性。

    先尝试 ``litellm.amodels()``（轻量级，不消耗 token），若不可用
    则使用第一个启用的模型发送极简 chat completion（``max_tokens=1``）。

    超时 10 秒。
    """
    svc = ProviderService(session)
    try:
        result = await svc.test_provider(provider_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ApiResponse(data=ProviderTestResultOut(**result))


@router.put("/providers/{provider_id}/toggle", response_model=ApiResponse, dependencies=[Depends(require_permission("model.manage"))])
async def toggle_provider(
    provider_id: uuid.UUID,
    enabled: bool,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """启用/禁用提供商。"""
    svc = ProviderService(session)
    try:
        await svc.toggle_provider(provider_id, enabled)
    except ValueError as e:
        err_msg = str(e)
        # B-05: re-test requirement surfaces as 400 (not 404)
        if "needs connectivity test" in err_msg:
            raise HTTPException(status_code=400, detail=err_msg)
        raise HTTPException(status_code=404, detail=err_msg)
    return ApiResponse(message=f"Provider {'enabled' if enabled else 'disabled'}")


@router.delete("/providers/{provider_id}", response_model=ApiResponse, dependencies=[Depends(require_permission("model.manage"))])
async def delete_provider(
    provider_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """删除提供商及其加密密钥。"""
    svc = ProviderService(session)
    try:
        await svc.delete_provider(provider_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ApiResponse(message="Provider and encrypted key deleted")


# =============================================================================
# Available Models (aggregated from all providers)
# =============================================================================


@router.get("/", response_model=ApiResponse[list[dict]], dependencies=[Depends(require_permission("model.read"))])
async def list_models(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """列出当前租户所有可用模型（聚合自所有启用的 Provider）。"""
    svc = ProviderService(session)
    providers = await svc.list_providers(ctx.tenant_id)

    all_models = []
    for p in providers:
        if not p["is_enabled"]:
            continue
        for model_cfg in p.get("models", []):
            # Backward compatible: missing enabled means True
            if model_cfg.get("enabled") is False:
                continue
            all_models.append({
                "model_name": model_cfg.get("name", "unknown"),
                "provider_name": p["provider_name"],
                "provider_display": p.get("display_name"),
                "context_length": model_cfg.get("context_length"),
                "purposes": model_cfg.get("purposes", []),
                "enabled": model_cfg.get("enabled", True),
                "priority": p.get("priority", 0),
            })

    # Sort by priority DESC
    all_models.sort(key=lambda x: x.get("priority", 0), reverse=True)
    return ApiResponse(data=all_models)


@router.get("/available", response_model=ApiResponse[list[dict]], dependencies=[Depends(require_permission("model.read"))])
async def list_available_models(
    purpose: str | None = None,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """列出当前租户所有可用模型，支持按用途过滤（供前端 ModelSelector 使用）。"""
    from ai_platform.services.model_resolver import ModelResolverService

    resolver = ModelResolverService(session)
    items = await resolver.list_available(ctx.tenant_id, purpose=purpose)
    return ApiResponse(
        data=[
            {
                "model_name": item.model_name,
                "provider_name": item.provider_name,
                "provider_display": item.provider_display,
                "purposes": item.purposes,
                "context_length": item.context_length,
                "priority": item.priority,
                "enabled": item.enabled,
            }
            for item in items
        ]
    )


# =============================================================================
# Purpose-based model resolution
# =============================================================================


class DefaultConfigOut(BaseModel):
    """Public default-config response (no credentials)."""

    provider_name: str
    provider_display: str | None
    model_name: str
    purposes: list[str]
    context_length: int | None
    source: str = Field(description="'db' or 'env'")


@router.get(
    "/default-config/{purpose}",
    response_model=ApiResponse[DefaultConfigOut],
    dependencies=[Depends(require_permission("model.read"))],
)
async def get_default_config(
    purpose: str,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """返回指定用途下优先级最高的模型配置（不含密钥，普通用户可用）。"""
    if purpose not in VALID_PURPOSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid purpose '{purpose}'. Valid values: {sorted(VALID_PURPOSES)}",
        )

    resolver = ModelResolverService(session)
    config = await resolver.get_default_for_purpose(ctx.tenant_id, purpose)

    if config is None:
        # Try env fallback
        config = resolver.get_env_fallback(purpose)
        if config is None:
            raise HTTPException(
                status_code=404,
                detail=f"No model configured for purpose '{purpose}'",
            )
        return ApiResponse(
            data=DefaultConfigOut(
                provider_name=config.provider_name,
                provider_display=config.provider_display,
                model_name=config.model_name,
                purposes=config.purposes,
                context_length=config.context_length,
                source="env",
            )
        )

    return ApiResponse(
        data=DefaultConfigOut(
            provider_name=config.provider_name,
            provider_display=config.provider_display,
            model_name=config.model_name,
            purposes=config.purposes,
            context_length=config.context_length,
            source="db",
        )
    )


class DefaultInternalOut(BaseModel):
    """Internal default response (includes decrypted credentials)."""

    provider_name: str
    provider_display: str | None
    model_name: str
    api_base_url: str | None
    api_key: str | None
    purposes: list[str]
    context_length: int | None
    source: str


@router.get(
    "/default/{purpose}",
    response_model=ApiResponse[DefaultInternalOut],
    dependencies=[Depends(require_permission("model.manage"))],
)
async def get_default_internal(
    purpose: str,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """返回指定用途下优先级最高的模型配置（含解密密钥）。

    ⚠️  仅限内部服务或管理员调用 — 响应中包含明文 API Key。
    需要 ``model.manage`` 权限（或 super-admin / ``*`` 超级权限）。
    """
    if purpose not in VALID_PURPOSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid purpose '{purpose}'. Valid values: {sorted(VALID_PURPOSES)}",
        )

    resolver = ModelResolverService(session)
    config = await resolver.get_default_for_purpose(ctx.tenant_id, purpose)

    if config is None:
        config = resolver.get_env_fallback(purpose)
        if config is None:
            raise HTTPException(
                status_code=404,
                detail=f"No model configured for purpose '{purpose}'",
            )
        return ApiResponse(
            data=DefaultInternalOut(
                provider_name=config.provider_name,
                provider_display=config.provider_display,
                model_name=config.model_name,
                api_base_url=config.api_base_url,
                api_key=config.api_key,
                purposes=config.purposes,
                context_length=config.context_length,
                source="env",
            )
        )

    return ApiResponse(
        data=DefaultInternalOut(
            provider_name=config.provider_name,
            provider_display=config.provider_display,
            model_name=config.model_name,
            api_base_url=config.api_base_url,
            api_key=config.api_key,
            purposes=config.purposes,
            context_length=config.context_length,
            source="db",
        )
    )


# =============================================================================
# Per-model toggle (inside a provider)
# =============================================================================


@router.post(
    "/providers/{provider_id}/models/{model_name}/toggle",
    response_model=ApiResponse,
    dependencies=[Depends(require_permission("model.manage"))],
)
async def toggle_model_enabled(
    provider_id: uuid.UUID,
    model_name: str,
    req: ModelToggleRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """设置单个模型的启用/禁用状态（前端控制目标状态）。"""
    resolver = ModelResolverService(session)
    try:
        new_enabled = await resolver.set_model_enabled(
            ctx.tenant_id, provider_id, model_name, req.enabled
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return ApiResponse(
        message=f"Model '{model_name}' {'enabled' if new_enabled else 'disabled'}"
    )
