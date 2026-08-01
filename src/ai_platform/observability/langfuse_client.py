"""Langfuse observability integration — LLM tracing + cost tracking."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog

from ai_platform.config import get_settings

logger = structlog.get_logger()

_langfuse_client = None


def get_langfuse():
    """Get or create Langfuse client singleton."""
    global _langfuse_client
    if _langfuse_client is None:
        settings = get_settings()
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            try:
                from langfuse import Langfuse

                _langfuse_client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                )
                logger.info("Langfuse initialized", host=settings.langfuse_host)
            except Exception as e:
                logger.warning("Langfuse init failed — tracing disabled", error=str(e))
                _langfuse_client = None
        else:
            logger.info("Langfuse not configured — tracing disabled")
    return _langfuse_client


@asynccontextmanager
async def trace(
    name: str,
    *,
    input_data: Any = None,
    metadata: dict | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> AsyncIterator:
    """
    Context manager for creating a Langfuse trace.

    Usage:
        async with trace("chat_completion", input_data={"model": "gpt-4o"}) as t:
            response = await llm.chat(request)
            t.update(output=response)
    """
    lf = get_langfuse()
    if lf is None:
        yield _NoopTrace()
        return

    trace_obj = lf.trace(
        name=name,
        input=input_data,
        metadata=metadata or {},
        user_id=user_id,
        session_id=session_id,
        tags=tags or [],
    )

    try:
        yield trace_obj
    except Exception as e:
        trace_obj.update(
            metadata={"error": str(e), "error_type": type(e).__name__},
            tags=["error"],
        )
        raise
    finally:
        lf.flush()


def create_generation(
    trace_id: str,
    name: str,
    *,
    model: str,
    input_messages: list[dict] | None = None,
    output: str | None = None,
    usage: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Record an LLM generation event in Langfuse."""
    lf = get_langfuse()
    if lf is None:
        return

    try:
        lf.generation(
            trace_id=trace_id,
            name=name,
            model=model,
            input=input_messages,
            output=output,
            usage=usage,
            metadata=metadata or {},
        )
    except Exception as e:
        logger.warning("Langfuse generation log failed", error=str(e))


class _NoopTrace:
    """No-op trace object when Langfuse is not configured."""

    trace_id: str = str(uuid.uuid4())

    def update(self, **kwargs: Any) -> None:
        pass

    def span(self, **kwargs: Any) -> _NoopTrace:
        return self

    def generation(self, **kwargs: Any) -> _NoopTrace:
        return self
