"""Costs API — /api/v1/costs/*."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.schemas.common import ApiResponse
from ai_platform.infra.database.connection import get_db
from ai_platform.services.cost_service import CostService

router = APIRouter()


class BudgetCheckRequest(BaseModel):
    monthly_budget_usd: float = Field(gt=0, description="Monthly budget in USD")


@router.get("/summary", response_model=ApiResponse)
async def get_cost_summary(
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    app_id: uuid.UUID | None = Query(default=None),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get aggregated cost summary (by model, tokens, requests)."""
    svc = CostService(session)
    summary = await svc.get_cost_summary(
        ctx.tenant_id,
        start_date=start_date,
        end_date=end_date,
        app_id=app_id,
    )
    return ApiResponse(data=summary.__dict__)


@router.get("/daily", response_model=ApiResponse)
async def get_daily_costs(
    days: int = Query(default=30, ge=1, le=365),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get daily cost breakdown for the past N days."""
    svc = CostService(session)
    daily = await svc.get_daily_costs(ctx.tenant_id, days=days)
    return ApiResponse(data=daily)


@router.post("/budget-check", response_model=ApiResponse)
async def check_budget(
    req: BudgetCheckRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Check current month's spend against a budget."""
    svc = CostService(session)
    result = await svc.check_budget(ctx.tenant_id, req.monthly_budget_usd)
    return ApiResponse(data=result)
