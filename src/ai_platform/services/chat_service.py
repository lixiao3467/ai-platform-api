"""Chat service — business logic for chat completions and conversation management."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
)
from ai_platform.core.model_router.litellm_client import LiteLLMClient
from ai_platform.domain.models import Conversation, Message

logger = structlog.get_logger()


class ChatService:
    """
    Orchestrates chat completions with conversation persistence.

    Flow:
    1. Load or create conversation
    2. Append user message to history
    3. Build context (recent messages)
    4. Call LiteLLM
    5. Persist assistant response
    6. Return response (streaming or non-streaming)
    """

    def __init__(self, llm_client: LiteLLMClient, session: AsyncSession) -> None:
        self._llm = llm_client
        self._db = session

    async def complete(
        self,
        request: ChatCompletionRequest,
        tenant_id: uuid.UUID,
        app_id: uuid.UUID | None = None,
    ) -> ChatCompletionResponse:
        """Non-streaming chat completion with conversation persistence."""

        # 1. Get or create conversation
        conversation = await self._get_or_create_conversation(
            request, tenant_id, app_id
        )

        # 2. Save user message
        user_msg = request.messages[-1]
        await self._save_message(conversation.id, user_msg)

        # 3. Load conversation history for context
        history = await self._load_history(conversation.id)

        # 4. Build request with full context
        context_request = request.model_copy(
            update={"messages": history, "conversation_id": conversation.id}
        )

        # 5. Call LLM
        response = await self._llm.chat(context_request)
        response.conversation_id = conversation.id

        # 6. Save assistant response
        if response.choices:
            assistant_msg = ChatMessage(
                role="assistant",
                content=response.choices[0].message.content,
                tool_calls=response.choices[0].message.tool_calls,
            )
            await self._save_message(
                conversation.id,
                assistant_msg,
                model=response.model,
                token_count=response.usage.completion_tokens,
            )

        # 7. Update conversation stats
        conversation.message_count += 2  # user + assistant
        conversation.total_tokens += response.usage.total_tokens
        await self._db.flush()

        return response

    async def complete_stream(
        self,
        request: ChatCompletionRequest,
        tenant_id: uuid.UUID,
        app_id: uuid.UUID | None = None,
    ) -> AsyncIterator[str]:
        """
        Streaming chat completion.

        Yields SSE-formatted chunks. Persists the full response after streaming completes.
        If the client disconnects mid-stream, the partial content is still persisted.
        """
        # 1. Get or create conversation
        conversation = await self._get_or_create_conversation(
            request, tenant_id, app_id
        )

        # 2. Save user message
        user_msg = request.messages[-1]
        await self._save_message(conversation.id, user_msg)

        # 3. Load history
        history = await self._load_history(conversation.id)

        # 4. Build context request
        context_request = request.model_copy(
            update={"messages": history, "conversation_id": conversation.id}
        )

        # 5. Stream from LLM, collecting content for persistence
        collected_content = []
        conversation_id = conversation.id

        # First chunk includes conversation_id
        import json

        yield f"data: {json.dumps({'conversation_id': str(conversation_id)})}\n\n"

        try:
            async for chunk in self._llm.chat_stream(context_request):
                # Extract content for persistence
                if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
                    try:
                        data = json.loads(chunk[6:].strip())
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        if delta.get("content"):
                            collected_content.append(delta["content"])
                    except (json.JSONDecodeError, IndexError, KeyError):
                        pass
                yield chunk
        except asyncio.CancelledError:
            # Client disconnected mid-stream — persist partial content and re-raise
            logger.warning(
                "Client disconnected during stream",
                conversation_id=str(conversation_id),
                partial_content_len=sum(len(c) for c in collected_content),
            )
            await self._persist_stream_result(
                conversation, collected_content, request.model
            )
            raise

        # 6. Persist full assistant message
        await self._persist_stream_result(
            conversation, collected_content, request.model
        )

    async def _persist_stream_result(
        self,
        conversation: Conversation,
        collected_content: list[str],
        model: str,
    ) -> None:
        """Persist streamed assistant content. Safe to call even if stream was interrupted."""
        full_content = "".join(collected_content)
        if full_content:
            try:
                await self._save_message(
                    conversation.id,
                    ChatMessage(role="assistant", content=full_content),
                    model=model,
                )
                conversation.message_count += 2
                await self._db.flush()
            except Exception as e:
                # Stream persistence failure shouldn't crash the stream
                logger.error(
                    "Failed to persist streamed message",
                    conversation_id=str(conversation.id),
                    error=str(e),
                )

    async def _get_or_create_conversation(
        self,
        request: ChatCompletionRequest,
        tenant_id: uuid.UUID,
        app_id: uuid.UUID | None,
    ) -> Conversation:
        """Load existing conversation or create a new one."""
        if request.conversation_id:
            conv = await self._db.get(Conversation, request.conversation_id)
            if conv:
                return conv

        # Create new conversation
        conv = Conversation(
            id=uuid.uuid4(),
            app_id=app_id or uuid.UUID("00000000-0000-0000-0000-000000000001"),
            tenant_id=tenant_id,
            user_id=request.user_id,
            model=request.model,
        )
        # Auto-generate title from first user message
        for msg in request.messages:
            if msg.role == "user" and msg.content:
                conv.title = msg.content[:100]
                break

        self._db.add(conv)
        await self._db.flush()
        return conv

    async def _load_history(self, conversation_id: uuid.UUID, limit: int = 50) -> list[ChatMessage]:
        """Load recent message history for context."""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        messages = result.scalars().all()

        return [
            ChatMessage(
                role=msg.role,
                content=msg.content,
                tool_call_id=msg.tool_call_id,
            )
            for msg in messages
        ]

    async def _save_message(
        self,
        conversation_id: uuid.UUID,
        msg: ChatMessage,
        *,
        model: str | None = None,
        token_count: int | None = None,
    ) -> Message:
        """Persist a message to the database."""
        db_msg = Message(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role=msg.role,
            content=msg.content,
            tool_calls=[tc.model_dump() for tc in msg.tool_calls] if msg.tool_calls else None,
            tool_call_id=msg.tool_call_id,
            model=model,
            token_count=token_count,
        )
        self._db.add(db_msg)
        await self._db.flush()
        return db_msg
