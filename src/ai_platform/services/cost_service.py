"""Cost management — token cost attribution, budget quotas, alerts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.domain.models import AuditLog

logger = structlog.get_logger()


# =============================================================================
# Model Pricing (per 1K tokens, USD)
# =============================================================================

MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "qwen-max": {"input": 0.002, "output": 0.006},
    "qwen-plus": {"input": 0.0004, "output": 0.0012},
    "qwen-turbo": {"input": 0.0001, "output": 0.0003},
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-reasoner": {"input": 0.00055, "output": 0.00219},
    "text-embedding-3-small": {"input": 0.00002, "output": 0.0},
    "text-embedding-3-large": {"input": 0.00013, "output": 0.0},
}

DEFAULT_PRICING = {"input": 0.003, "output": 0.01}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in USD for a given model and token counts."""
    pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
    input_cost = (input_tokens / 1000) * pricing["input"]
    output_cost = (output_tokens / 1000) * pricing["output"]
    return round(input_cost + output_cost, 6)


# =============================================================================
# Cost Service
# =============================================================================


@dataclass
class CostSummary:
    """Aggregated cost data."""

    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_requests: int
    by_model: dict[str, dict[str, Any]]
    period_start: str
    period_end: str


class CostService:
    """
    Cost tracking and budget management.

    - Aggregates token costs from audit_logs
    - Supports filtering by tenant, app, model, time range
    - Budget quota management with alert thresholds
    """

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def get_cost_summary(
        self,
        tenant_id: uuid.UUID,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        app_id: uuid.UUID | None = None,
    ) -> CostSummary:
        """Get aggregated cost summary for a tenant."""
        now = datetime.now(tz=timezone.utc)
        if not start_date:
            start_date = now.replace(day=1, hour=0, minute=0, second=0)
        if not end_date:
            end_date = now

        # Base query
        conditions = [
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= start_date,
            AuditLog.created_at <= end_date,
            AuditLog.token_input.isnot(None),
        ]
        if app_id:
            conditions.append(AuditLog.app_id == app_id)

        # Total aggregation
        stmt = select(
            func.coalesce(func.sum(AuditLog.token_input), 0).label("total_input"),
            func.coalesce(func.sum(AuditLog.token_output), 0).label("total_output"),
            func.count(AuditLog.id).label("total_requests"),
        ).where(and_(*conditions))

        result = await self._db.execute(stmt)
        row = result.first()
        total_input = row.total_input or 0
        total_output = row.total_output or 0
        total_requests = row.total_requests or 0

        # Per-model breakdown — extract model from action or request_data
        model_stmt = select(
            AuditLog.request_data,
            AuditLog.token_input,
            AuditLog.token_output,
        ).where(and_(*conditions))
        model_result = await self._db.execute(model_stmt)
        model_rows = model_result.all()

        by_model: dict[str, dict[str, Any]] = {}
        total_cost = 0.0

        for row in model_rows:
            req_data = row.request_data or {}
            model = req_data.get("model", "unknown")
            inp = row.token_input or 0
            out = row.token_output or 0

            if model not in by_model:
                by_model[model] = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "requests": 0,
                    "cost_usd": 0.0,
                }

            cost = calculate_cost(model, inp, out)
            by_model[model]["input_tokens"] += inp
            by_model[model]["output_tokens"] += out
            by_model[model]["requests"] += 1
            by_model[model]["cost_usd"] = round(by_model[model]["cost_usd"] + cost, 6)
            total_cost += cost

        return CostSummary(
            total_cost_usd=round(total_cost, 4),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_requests=total_requests,
            by_model=by_model,
            period_start=start_date.isoformat(),
            period_end=end_date.isoformat(),
        )

    async def get_daily_costs(
        self,
        tenant_id: uuid.UUID,
        *,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Get daily cost breakdown for the past N days."""
        now = datetime.now(tz=timezone.utc)
        daily: list[dict[str, Any]] = []

        for i in range(days):
            day_start = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - __import__("datetime").timedelta(days=i)
            day_end = day_start + __import__("datetime").timedelta(days=1)

            conditions = [
                AuditLog.tenant_id == tenant_id,
                AuditLog.created_at >= day_start,
                AuditLog.created_at < day_end,
                AuditLog.token_input.isnot(None),
            ]

            stmt = select(
                func.coalesce(func.sum(AuditLog.token_input), 0),
                func.coalesce(func.sum(AuditLog.token_output), 0),
                func.count(AuditLog.id),
            ).where(and_(*conditions))

            result = await self._db.execute(stmt)
            row = result.first()

            inp = row[0] or 0
            out = row[1] or 0
            count = row[2] or 0

            # Estimate cost (use default pricing as approximation)
            est_cost = (inp / 1000) * 0.003 + (out / 1000) * 0.01

            daily.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "input_tokens": inp,
                "output_tokens": out,
                "requests": count,
                "estimated_cost_usd": round(est_cost, 4),
            })

        return list(reversed(daily))  # chronological order

    async def check_budget(
        self,
        tenant_id: uuid.UUID,
        monthly_budget_usd: float,
    ) -> dict[str, Any]:
        """Check current month's spend against budget."""
        now = datetime.now(tz=timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        summary = await self.get_cost_summary(
            tenant_id, start_date=month_start, end_date=now
        )

        spent = summary.total_cost_usd
        remaining = max(0, monthly_budget_usd - spent)
        usage_pct = (spent / monthly_budget_usd * 100) if monthly_budget_usd > 0 else 0

        alerts = []
        if usage_pct >= 100:
            alerts.append("BUDGET_EXCEEDED: Monthly budget has been exceeded")
        elif usage_pct >= 80:
            alerts.append("BUDGET_WARNING: 80% of monthly budget consumed")

        return {
            "monthly_budget_usd": monthly_budget_usd,
            "spent_usd": round(spent, 4),
            "remaining_usd": round(remaining, 4),
            "usage_percentage": round(usage_pct, 1),
            "alerts": alerts,
            "period_start": month_start.isoformat(),
            "period_end": now.isoformat(),
        }
