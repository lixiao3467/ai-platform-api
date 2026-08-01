"""LiteLLM client — unified interface to the AI Gateway."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import litellm
import structlog

from ai_platform.api.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamChunk,
    ChatMessage,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    StreamChoice,
    ToolCall,
    ToolCallFunction,
    UsageInfo,
)
from ai_platform.config import get_settings

logger = structlog.get_logger()

# Suppress LiteLLM's verbose logging
litellm.suppress_debug_info = True


class LiteLLMClient:
    """
    Async client wrapping LiteLLM for model calls via the AI Gateway.

    All model calls go through this client, which provides:
    - OpenAI-compatible request/response format
    - Streaming support with SSE
    - Error handling and retry
    - Usage tracking

    API Key Resolution (priority order):
    1. Key passed directly via resolve_key() at call time (from DB)
    2. LiteLLM master key from config (for proxy mode)
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._api_base = settings.litellm_api_base
        self._api_key = settings.litellm_master_key

    async def chat(
        self,
        request: ChatCompletionRequest,
        *,
        tenant_id: uuid.UUID | None = None,
        db_session: Any | None = None,
    ) -> ChatCompletionResponse:
        """Non-streaming chat completion."""
        messages = self._to_litellm_messages(request.messages)

        # Resolve API key: DB first, then config fallback
        api_key, api_base = await self._resolve_credentials(
            request.model, tenant_id, db_session
        )

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "presence_penalty": request.presence_penalty,
            "frequency_penalty": request.frequency_penalty,
            "api_base": api_base,
            "api_key": api_key,
        }
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        if request.stop:
            kwargs["stop"] = request.stop
        if request.tools:
            kwargs["tools"] = request.tools
            if request.tool_choice:
                kwargs["tool_choice"] = request.tool_choice

        start_time = time.time()

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as e:
            logger.error("LiteLLM call failed", model=request.model, error=str(e))
            raise

        elapsed = time.time() - start_time
        logger.info(
            "LiteLLM call completed",
            model=request.model,
            elapsed_ms=round(elapsed * 1000),
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
        )

        # Langfuse tracing — record LLM generation
        from ai_platform.observability.langfuse_client import create_generation

        create_generation(
            trace_id=str(uuid.uuid4()),
            name="chat_completion",
            model=request.model,
            input_messages=messages,
            output=response.choices[0].message.content if response.choices else None,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            } if response.usage else None,
            metadata={"elapsed_ms": round(elapsed * 1000)},
        )

        # Build our response model
        choices = []
        for c in response.choices:
            tool_calls = None
            if c.message.tool_calls:
                tool_calls = [
                    ToolCall(
                        id=tc.id,
                        type=tc.type,
                        function=ToolCallFunction(
                            name=tc.function.name,
                            arguments=tc.function.arguments,
                        ),
                    )
                    for tc in c.message.tool_calls
                ]

            choices.append(
                Choice(
                    index=c.index,
                    message=ChoiceMessage(
                        role=c.message.role,
                        content=c.message.content,
                        tool_calls=tool_calls,
                    ),
                    finish_reason=c.finish_reason,
                )
            )

        usage = UsageInfo(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
        )

        return ChatCompletionResponse(
            id=response.id or f"chatcmpl-{uuid.uuid4().hex[:8]}",
            created=int(time.time()),
            model=response.model or request.model,
            choices=choices,
            usage=usage,
            conversation_id=request.conversation_id,
        )

    async def chat_stream(
        self,
        request: ChatCompletionRequest,
        *,
        tenant_id: uuid.UUID | None = None,
        db_session: Any | None = None,
    ) -> AsyncIterator[str]:
        """
        Streaming chat completion — yields SSE-formatted strings.

        Each chunk is: "data: {json}\\n\\n"
        Final chunk is: "data: [DONE]\\n\\n"
        """
        import json

        messages = self._to_litellm_messages(request.messages)

        # Resolve API key: DB first, then config fallback
        api_key, api_base = await self._resolve_credentials(
            request.model, tenant_id, db_session
        )

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": True,
            "api_base": api_base,
            "api_key": api_key,
        }
        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        if request.stop:
            kwargs["stop"] = request.stop
        if request.tools:
            kwargs["tools"] = request.tools
            if request.tool_choice:
                kwargs["tool_choice"] = request.tool_choice

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created = int(time.time())

        try:
            response = await litellm.acompletion(**kwargs)

            async for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue

                stream_chunk = ChatCompletionStreamChunk(
                    id=completion_id,
                    created=created,
                    model=chunk.model or request.model,
                    choices=[
                        StreamChoice(
                            index=0,
                            delta=DeltaMessage(
                                role=delta.role,
                                content=delta.content,
                            ),
                            finish_reason=(
                                chunk.choices[0].finish_reason if chunk.choices else None
                            ),
                        )
                    ],
                )
                yield f"data: {stream_chunk.model_dump_json(exclude_none=True)}\n\n"

        except Exception as e:
            logger.error("LiteLLM stream failed", model=request.model, error=str(e))
            error_data = json.dumps({"error": {"message": str(e), "type": "server_error"}})
            yield f"data: {error_data}\n\n"

        yield "data: [DONE]\n\n"

    @staticmethod
    def _to_litellm_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Convert our ChatMessage list to LiteLLM's dict format."""
        result = []
        for msg in messages:
            d: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                d["content"] = msg.content
            if msg.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            if msg.name:
                d["name"] = msg.name
            result.append(d)
        return result

    async def _resolve_credentials(
        self,
        model_name: str,
        tenant_id: uuid.UUID | None,
        db_session: Any | None,
    ) -> tuple[str, str]:
        """
        Resolve API key and base URL for a model.

        Priority:
        1. Database lookup (via ProviderService) — production path
        2. LiteLLM master key fallback — dev/proxy path

        Returns: (api_key, api_base_url)
        """
        if tenant_id and db_session:
            try:
                from ai_platform.services.provider_service import ProviderService

                svc = ProviderService(db_session)
                api_key, api_base = await svc.get_key_for_model(tenant_id, model_name)
                if api_key:
                    return api_key, (api_base or self._api_base)
            except Exception as e:
                logger.warning(
                    "DB key resolution failed, falling back to config",
                    model=model_name,
                    error=str(e),
                )

        # Fallback: use LiteLLM proxy master key
        return self._api_key, self._api_base


# Singleton
_llm_client: LiteLLMClient | None = None


def get_llm_client() -> LiteLLMClient:
    """Get or create the LiteLLM client singleton."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LiteLLMClient()
    return _llm_client
