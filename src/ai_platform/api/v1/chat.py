"""Chat API endpoints — /api/v1/chat/*."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.api.schemas.chat import ChatCompletionRequest
from ai_platform.core.model_router.litellm_client import get_llm_client
from ai_platform.infra.database.connection import get_db
from ai_platform.services.chat_service import ChatService

router = APIRouter()


@router.post("/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    ctx: RequestContext = Depends(get_request_context),
    session: AsyncSession = Depends(get_db),
):
    """
    Chat completion — OpenAI-compatible endpoint.

    Supports both streaming (stream=true) and non-streaming responses.
    Automatically manages conversation history.
    """
    service = ChatService(get_llm_client(), session)

    if request.stream:
        return StreamingResponse(
            service.complete_stream(request, ctx.tenant_id, ctx.app_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return await service.complete(request, ctx.tenant_id, ctx.app_id)
