"""Models & Providers API — /api/v1/models/*."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.schemas.common import ApiResponse
from ai_platform.infra.database.connection import get_db
from ai_platform.services.provider_service import ProviderService

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


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
    models: list[dict[str, Any]] = Field(
        default_factory=list,
        description='Model list, e.g. [{"name": "gpt-4o", "context_length": 128000}]',
    )
    priority: int = Field(default=0, description="Higher = preferred in routing")


class ProviderUpdateKeyRequest(BaseModel):
    api_key: str = Field(description="New API key — will be encrypted before storage")


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


# =============================================================================
# Provider CRUD — 密钥通过后台管理，加密存储
# =============================================================================


@router.post("/providers", response_model=ApiResponse[ProviderOut])
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
        models=req.models,
        priority=req.priority,
    )

    # Return with masked key
    providers = await svc.list_providers(ctx.tenant_id)
    provider_data = next(
        (p for p in providers if p["id"] == str(provider.id)), None
    )

    return ApiResponse(data=ProviderOut(**provider_data))


@router.get("/providers", response_model=ApiResponse[list[ProviderOut]])
async def list_providers(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """列出当前租户的所有模型提供商（密钥已脱敏）。"""
    svc = ProviderService(session)
    providers = await svc.list_providers(ctx.tenant_id)
    return ApiResponse(data=[ProviderOut(**p) for p in providers])


@router.put("/providers/{provider_id}/key", response_model=ApiResponse)
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


@router.put("/providers/{provider_id}/toggle", response_model=ApiResponse)
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
        raise HTTPException(status_code=404, detail=str(e))
    return ApiResponse(message=f"Provider {'enabled' if enabled else 'disabled'}")


@router.delete("/providers/{provider_id}", response_model=ApiResponse)
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


@router.get("/", response_model=ApiResponse[list[dict]])
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
            all_models.append({
                "model_name": model_cfg.get("name", "unknown"),
                "provider": p["provider_name"],
                "display_name": p.get("display_name"),
                "context_length": model_cfg.get("context_length"),
                "capabilities": model_cfg.get("capabilities", []),
            })

    return ApiResponse(data=all_models)
