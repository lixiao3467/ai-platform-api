"""Audit Logs API — /api/v1/audit-logs/*."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.domain.models import AuditLog
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class AuditLogOut(BaseModel):
    id: int
    tenant_id: str | None = None
    app_id: str | None = None
    user_id: str | None = None
    api_key_prefix: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    request_data: dict | None = None
    response_code: int | None = None
    token_input: int | None = None
    token_output: int | None = None
    latency_ms: int | None = None
    ip_address: str | None = None
    trace_id: str | None = None
    created_at: str


class AuditStatsOut(BaseModel):
    """Aggregated audit statistics for a time range."""

    total_requests: int
    success_count: int
    error_count: int
    avg_latency_ms: float
    by_action: dict[str, int] = Field(default_factory=dict)
    by_resource: dict[str, int] = Field(default_factory=dict)
    period_start: str
    period_end: str


# =============================================================================
# Action mapping for human-readable labels
# =============================================================================

ACTION_LABELS: dict[str, str] = {
    "chat.post": "对话生成",
    "conversation.get": "查看会话",
    "conversation.delete": "删除会话",
    "agent.post": "创建智能体",
    "agent.put": "更新智能体",
    "agent.delete": "删除智能体",
    "knowledge-base.post": "创建知识库",
    "knowledge-base.delete": "删除知识库",
    "provider.post": "创建模型提供者",
    "provider.put": "更新模型提供者",
    "provider.delete": "删除模型提供者",
    "prompt.post": "创建提示词",
    "prompt.put": "更新提示词",
    "prompt.delete": "删除提示词",
    "workflow.post": "创建工作流",
    "workflow.put": "更新工作流",
    "workflow.delete": "删除工作流",
    "user.post": "创建用户",
    "user.put": "更新用户",
    "user.delete": "删除用户",
    "role.post": "创建角色",
    "role.put": "更新角色",
    "role.delete": "删除角色",
    "auth.post": "登录认证",
}


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "/",
    response_model=ApiResponse[PaginatedResponse[AuditLogOut]],
    summary="查询审计日志",
    description="分页查询审计日志。支持按操作人、时间范围、操作类型、资源类型过滤。",
    dependencies=[Depends(require_permission("audit.view"))],
    responses={
        200: {"description": "成功返回分页列表"},
        400: {"description": "请求参数错误"},
    },
)
async def list_audit_logs(
    page: int = Query(default=1, ge=1, description="页码（从1开始）"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    user_id: str | None = Query(default=None, description="按操作人 ID 过滤"),
    action: str | None = Query(default=None, description="按操作类型过滤（如 chat.post, agent.delete）"),
    resource_type: str | None = Query(default=None, description="按资源类型过滤（如 agent, conversation）"),
    resource_id: str | None = Query(default=None, description="按资源 ID 过滤"),
    start_time: datetime | None = Query(default=None, alias="start_time", description="开始时间（ISO 8601）"),
    end_time: datetime | None = Query(default=None, alias="end_time", description="结束时间（ISO 8601）"),
    response_code_min: int | None = Query(default=None, ge=100, le=599, description="最小响应码（如 400 查错误）"),
    response_code_max: int | None = Query(default=None, ge=100, le=599, description="最大响应码（如 499）"),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """
    分页查询审计日志。

    - 自动按当前租户隔离（tenant_id）
    - 支持多维度过滤
    - 默认按时间倒序
    """
    # Base conditions: tenant isolation
    conditions = [AuditLog.tenant_id == ctx.tenant_id]

    if ctx.app_id:
        conditions.append(AuditLog.app_id == ctx.app_id)

    # Optional filters
    if user_id:
        conditions.append(AuditLog.user_id == user_id)
    if action:
        conditions.append(AuditLog.action == action)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)
    if resource_id:
        conditions.append(AuditLog.resource_id == resource_id)
    if start_time:
        conditions.append(AuditLog.created_at >= start_time)
    if end_time:
        conditions.append(AuditLog.created_at <= end_time)
    if response_code_min is not None:
        conditions.append(AuditLog.response_code >= response_code_min)
    if response_code_max is not None:
        conditions.append(AuditLog.response_code <= response_code_max)

    # Count total
    count_stmt = select(func.count()).select_from(AuditLog).where(and_(*conditions))
    total = (await session.execute(count_stmt)).scalar() or 0

    # Fetch page
    offset = (page - 1) * page_size
    stmt = (
        select(AuditLog)
        .where(and_(*conditions))
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    logs = result.scalars().all()

    items = [
        AuditLogOut(
            id=log.id,
            tenant_id=str(log.tenant_id) if log.tenant_id else None,
            app_id=str(log.app_id) if log.app_id else None,
            user_id=log.user_id,
            api_key_prefix=log.api_key_prefix,
            action=log.action,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            request_data=log.request_data,
            response_code=log.response_code,
            token_input=log.token_input,
            token_output=log.token_output,
            latency_ms=log.latency_ms,
            ip_address=log.ip_address,
            trace_id=log.trace_id,
            created_at=log.created_at.isoformat(),
        )
        for log in logs
    ]

    return ApiResponse(
        data=PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.get(
    "/stats",
    response_model=ApiResponse[AuditStatsOut],
    summary="审计统计",
    description="获取审计日志统计数据：请求总量、成功/失败次数、平均延迟、按操作类型/资源分布。",
    dependencies=[Depends(require_permission("audit.view"))],
)
async def audit_stats(
    start_time: datetime | None = Query(default=None, description="开始时间"),
    end_time: datetime | None = Query(default=None, description="结束时间"),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """审计日志统计 — 请求量、错误率、延迟分布。"""
    from datetime import datetime as dt, timedelta, timezone

    now = dt.now(tz=timezone.utc)
    if not end_time:
        end_time = now
    if not start_time:
        start_time = now - timedelta(days=7)

    conditions = [
        AuditLog.tenant_id == ctx.tenant_id,
        AuditLog.created_at >= start_time,
        AuditLog.created_at <= end_time,
    ]
    if ctx.app_id:
        conditions.append(AuditLog.app_id == ctx.app_id)

    # Overall stats
    overall_stmt = select(
        func.count().label("total"),
        func.count().filter(AuditLog.response_code < 400).label("success"),
        func.count().filter(AuditLog.response_code >= 400).label("errors"),
        func.avg(AuditLog.latency_ms).label("avg_latency"),
    ).where(and_(*conditions))

    row = (await session.execute(overall_stmt)).first()
    total = row.total or 0
    success = row.success or 0
    errors = row.errors or 0
    avg_latency = float(row.avg_latency or 0)

    # By-action distribution
    action_stmt = (
        select(AuditLog.action, func.count().label("cnt"))
        .where(and_(*conditions))
        .group_by(AuditLog.action)
    )
    action_result = await session.execute(action_stmt)
    by_action = {r.action: r.cnt for r in action_result.all()}

    # By-resource distribution
    resource_stmt = (
        select(AuditLog.resource_type, func.count().label("cnt"))
        .where(and_(*conditions))
        .where(AuditLog.resource_type.isnot(None))
        .group_by(AuditLog.resource_type)
    )
    resource_result = await session.execute(resource_stmt)
    by_resource = {r.resource_type: r.cnt for r in resource_result.all()}

    return ApiResponse(
        data=AuditStatsOut(
            total_requests=total,
            success_count=success,
            error_count=errors,
            avg_latency_ms=round(avg_latency, 1),
            by_action=by_action,
            by_resource=by_resource,
            period_start=start_time.isoformat(),
            period_end=end_time.isoformat(),
        )
    )


@router.get(
    "/actions",
    response_model=ApiResponse[dict],
    summary="可用操作类型列表",
    description="返回所有已记录的操作类型及其可读标签，用于前端下拉过滤。",
    dependencies=[Depends(require_permission("audit.view"))],
)
async def list_actions(
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """列出所有已记录的操作类型。"""
    stmt = (
        select(AuditLog.action)
        .where(AuditLog.tenant_id == ctx.tenant_id)
        .distinct()
        .order_by(AuditLog.action)
    )
    result = await session.execute(stmt)
    actions = [r.action for r in result.all()]

    return ApiResponse(
        data={
            "actions": actions,
            "labels": ACTION_LABELS,
        }
    )
