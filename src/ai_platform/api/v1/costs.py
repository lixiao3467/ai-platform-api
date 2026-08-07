"""Costs API — /api/v1/costs/*."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse
from ai_platform.infra.database.connection import get_db
from ai_platform.services.cost_service import CostService

router = APIRouter()


class BudgetCheckRequest(BaseModel):
    monthly_budget_usd: float = Field(gt=0, description="Monthly budget in USD")


class CostSummaryRequest(BaseModel):
    start_date: datetime | None = Field(default=None, description="Start date filter")
    end_date: datetime | None = Field(default=None, description="End date filter")
    app_id: str | None = Field(default=None, description="Filter by app ID (UUID)")


class DailyCostsRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=365, description="查询天数范围")


class CostExportRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=365, description="导出最近 N 天的数据")
    format: str = Field(default="csv", pattern="^(csv|json)$", description="导出格式")
    app_id: str | None = Field(default=None, description="按应用过滤 (UUID)")


@router.post("/summary", response_model=ApiResponse, dependencies=[Depends(require_permission("cost.read"))])
async def get_cost_summary(
    req: CostSummaryRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get aggregated cost summary (by model, tokens, requests)."""
    from uuid import UUID

    svc = CostService(session)
    summary = await svc.get_cost_summary(
        ctx.tenant_id,
        start_date=req.start_date,
        end_date=req.end_date,
        app_id=UUID(req.app_id) if req.app_id else None,
    )
    return ApiResponse(data=summary.__dict__)


@router.post("/daily", response_model=ApiResponse, dependencies=[Depends(require_permission("cost.read"))])
async def get_daily_costs(
    req: DailyCostsRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get daily cost breakdown for the past N days."""
    svc = CostService(session)
    daily = await svc.get_daily_costs(ctx.tenant_id, days=req.days)
    return ApiResponse(data=daily)


@router.post("/budget-check", response_model=ApiResponse, dependencies=[Depends(require_permission("cost.read"))])
async def check_budget(
    req: BudgetCheckRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Check current month's spend against a budget."""
    svc = CostService(session)
    result = await svc.check_budget(ctx.tenant_id, req.monthly_budget_usd)
    return ApiResponse(data=result)


@router.post(
    "/export",
    summary="导出成本数据",
    description="将每日成本明细导出为 CSV 或 JSON。支持大数据量流式下载。",
    dependencies=[Depends(require_permission("cost.read"))],
    responses={
        200: {"description": "文件下载", "content": {"text/csv": {}, "application/json": {}}},
    },
)
async def export_costs(
    req: CostExportRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Export daily cost data as CSV or JSON (streaming)."""
    svc = CostService(session)
    daily_data = await svc.get_daily_costs(ctx.tenant_id, days=req.days)

    if req.format == "json":
        import json
        import io

        content = json.dumps(daily_data, ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.StringIO(content),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="costs-{req.days}days.json"'},
        )

    # CSV
    import csv
    import io

    output = io.StringIO()
    output.write("")  # BOM for Excel
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(["日期", "输入 Tokens", "输出 Tokens", "请求数", "预估费用 (USD)"])
    for row in daily_data:
        writer.writerow([
            row.get("date", ""),
            row.get("input_tokens", 0),
            row.get("output_tokens", 0),
            row.get("requests", 0),
            f"{row.get('estimated_cost_usd', 0):.4f}",
        ])

    output.seek(0)
    return StreamingResponse(
        io.StringIO(output.getvalue()),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="costs-{req.days}days.csv"',
            "X-Accel-Buffering": "no",
        },
    )
