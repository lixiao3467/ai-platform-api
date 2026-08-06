"""Conversations API endpoints — /api/v1/conversations/*."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.middleware.permissions import require_permission
from ai_platform.api.schemas.common import ApiResponse, PaginatedResponse
from ai_platform.domain.models import Conversation, Message
from ai_platform.infra.database.connection import get_db

router = APIRouter()


# =============================================================================
# Schemas
# =============================================================================


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str | None
    model: str | None
    user_id: str | None
    message_count: int
    total_tokens: int
    status: str
    created_at: str


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str | None
    tool_calls: list | None = None
    tool_call_id: str | None = None
    model: str | None = None
    token_count: int | None = None
    created_at: str


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/", response_model=ApiResponse[PaginatedResponse[ConversationOut]], dependencies=[Depends(require_permission("audit.view"))])
async def list_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """List conversations for the current tenant/app."""
    offset = (page - 1) * page_size

    query = (
        select(Conversation)
        .where(Conversation.tenant_id == ctx.tenant_id)
        .order_by(Conversation.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    if ctx.app_id:
        query = query.where(Conversation.app_id == ctx.app_id)

    result = await session.execute(query)
    conversations = result.scalars().all()

    count_query = select(func.count()).select_from(Conversation).where(
        Conversation.tenant_id == ctx.tenant_id
    )
    total = (await session.execute(count_query)).scalar() or 0

    items = [
        ConversationOut(
            id=c.id,
            title=c.title,
            model=c.model,
            user_id=c.user_id,
            message_count=c.message_count,
            total_tokens=c.total_tokens,
            status=c.status,
            created_at=c.created_at.isoformat(),
        )
        for c in conversations
    ]

    return ApiResponse(
        data=PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/{conversation_id}", response_model=ApiResponse[ConversationOut], dependencies=[Depends(require_permission("audit.view"))])
async def get_conversation(
    conversation_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get a conversation by ID."""
    conv = await session.get(Conversation, conversation_id)
    if not conv or conv.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    return ApiResponse(
        data=ConversationOut(
            id=conv.id,
            title=conv.title,
            model=conv.model,
            user_id=conv.user_id,
            message_count=conv.message_count,
            total_tokens=conv.total_tokens,
            status=conv.status,
            created_at=conv.created_at.isoformat(),
        )
    )


@router.get("/{conversation_id}/messages", response_model=ApiResponse[list[MessageOut]], dependencies=[Depends(require_permission("audit.view"))])
async def get_messages(
    conversation_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Get messages for a conversation."""
    conv = await session.get(Conversation, conversation_id)
    if not conv or conv.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    messages = result.scalars().all()

    items = [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            tool_calls=m.tool_calls,
            tool_call_id=m.tool_call_id,
            model=m.model,
            token_count=m.token_count,
            created_at=m.created_at.isoformat(),
        )
        for m in messages
    ]

    return ApiResponse(data=items)


@router.get(
    "/{conversation_id}/messages/export",
    summary="导出会话消息",
    description="将指定会话的所有消息导出为 CSV 或 JSON 文件。支持大数据量流式下载。",
    dependencies=[Depends(require_permission("audit.view"))],
    responses={
        200: {"description": "文件下载", "content": {"text/csv": {}, "application/json": {}}},
        404: {"description": "会话不存在"},
    },
)
async def export_messages(
    conversation_id: uuid.UUID,
    format: str = Query(default="csv", pattern="^(csv|json)$", description="导出格式: csv 或 json"),
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Export all messages of a conversation as CSV/JSON (streaming)."""
    conv = await session.get(Conversation, conversation_id)
    if not conv or conv.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # Tenant isolation enforced above. Now stream messages.
    async def fetch_page(offset: int, limit: int) -> list[dict]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        messages = result.scalars().all()
        return [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content or "",
                "model": m.model or "",
                "token_count": m.token_count or 0,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ]

    safe_title = (conv.title or f"conversation-{conversation_id}")[:60]
    safe_title = safe_title.replace('"', '_').replace(',', '_')

    if format == "json":
        from ai_platform.api.export_utils import make_json_stream
        return make_json_stream(
            fetch_page,
            page_size=500,
            filename=f"{safe_title}-messages.json",
        )

    from ai_platform.api.export_utils import make_csv_stream
    return make_csv_stream(
        columns=["id", "role", "content", "model", "token_count", "created_at"],
        headers=["ID", "角色", "内容", "模型", "Token 数", "时间"],
        fetch_page=fetch_page,
        page_size=500,
        filename=f"{safe_title}-messages.csv",
    )


@router.delete("/{conversation_id}", response_model=ApiResponse, dependencies=[Depends(require_permission("audit.view"))])
async def delete_conversation(
    conversation_id: uuid.UUID,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """Delete a conversation and its messages."""
    conv = await session.get(Conversation, conversation_id)
    if not conv or conv.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # Bulk-delete messages in a single statement (avoids N+1 round-trips)
    await session.execute(delete(Message).where(Message.conversation_id == conversation_id))
    await session.delete(conv)
    return ApiResponse(message="Conversation deleted")
