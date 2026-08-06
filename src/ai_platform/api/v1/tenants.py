"""Tenant management API — /api/v1/tenants/* (super-admin CRUD)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.domain.models import Tenant
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9\-]*[a-z0-9]$")
    plan: str = Field(default="standard", max_length=32)
    admin_email: str | None = Field(default=None, max_length=128)
    quota_config: dict | None = None


class TenantUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    plan: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=16)
    quota_config: dict | None = None


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


@router.get(
    "/",
    response_model=ApiResponse[PaginatedResponse[TenantOut]],
    summary="租户列表",
    description="分页查询所有租户（超管视角）。支持按名称搜索、按状态过滤。",
    dependencies=[Depends(require_permission("tenant.read"))],
)
async def list_tenants(
    page: int = Query(default=1, ge=1, description="页码（从1开始）"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    search: str | None = Query(default=None, description="按租户名称模糊搜索"),
    status: str | None = Query(default=None, description="按状态过滤（active/disabled/...）"),
    session: AsyncSession = Depends(get_db),
):
    """分页租户列表 — 超管使用。"""
    conditions: list = []
    if search:
        conditions.append(Tenant.name.ilike(f"%{search}%"))
    if status:
        conditions.append(Tenant.status == status)

    where_clause = and_(*conditions) if conditions else True

    # Total count
    count_stmt = select(func.count()).select_from(Tenant).where(where_clause)
    total = (await session.execute(count_stmt)).scalar() or 0

    # Fetch page
    offset = (page - 1) * page_size
    stmt = (
        select(Tenant)
        .where(where_clause)
        .order_by(Tenant.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    tenants = result.scalars().all()

    return ApiResponse(
        data=PaginatedResponse(
            items=[_tenant_to_out(t) for t in tenants],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/",
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


@router.get(
    "/{tenant_id}",
    response_model=ApiResponse[TenantOut],
    summary="租户详情",
    description="获取单个租户详情。",
    dependencies=[Depends(require_permission("tenant.read"))],
)
async def get_tenant(
    tenant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
):
    """租户详情。"""
    tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")
    return ApiResponse(data=_tenant_to_out(tenant))


@router.put(
    "/{tenant_id}",
    response_model=ApiResponse[TenantOut],
    summary="更新租户",
    description="更新租户信息（名称、套餐、状态、配额等）。",
    dependencies=[Depends(require_permission("tenant.update"))],
)
async def update_tenant(
    tenant_id: uuid.UUID,
    req: TenantUpdateRequest,
    session: AsyncSession = Depends(get_db),
):
    """更新租户。"""
    tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
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


@router.delete(
    "/{tenant_id}",
    response_model=ApiResponse,
    summary="软删除租户",
    description="将租户状态设置为 disabled（软删除）。不会物理删除数据。",
    dependencies=[Depends(require_permission("tenant.delete"))],
)
async def delete_tenant(
    tenant_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
):
    """软删除租户 — 设置 status='disabled'。"""
    tenant = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    if tenant.status == "disabled":
        raise HTTPException(status_code=409, detail="租户已是禁用状态")

    tenant.status = "disabled"
    await session.flush()

    return ApiResponse(message="租户已禁用")
