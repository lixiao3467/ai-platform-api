"""Domain events — decoupled async event system."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Base Event
# =============================================================================


@dataclass
class DomainEvent:
    """Base class for all domain events."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return self.__class__.__name__


# =============================================================================
# Event Definitions
# =============================================================================


# --- Document Events ---

@dataclass
class DocumentUploadedEvent(DomainEvent):
    """Fired when a document is uploaded to a knowledge base."""

    document_id: str = ""
    kb_id: str = ""
    filename: str = ""
    mime_type: str = ""
    file_size: int = 0


@dataclass
class DocumentIngestedEvent(DomainEvent):
    """Fired when a document has been fully processed (parsed + chunked + embedded)."""

    document_id: str = ""
    kb_id: str = ""
    chunk_count: int = 0


@dataclass
class DocumentFailedEvent(DomainEvent):
    """Fired when document ingestion fails."""

    document_id: str = ""
    kb_id: str = ""
    error: str = ""


# --- Conversation Events ---

@dataclass
class ConversationCreatedEvent(DomainEvent):
    """Fired when a new conversation is created."""

    conversation_id: str = ""
    app_id: str = ""
    user_id: str | None = None
    model: str = ""


@dataclass
class MessageCompletedEvent(DomainEvent):
    """Fired when an LLM response is completed."""

    conversation_id: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


# --- Agent Events ---

@dataclass
class AgentExecutionStartedEvent(DomainEvent):
    """Fired when an agent starts executing."""

    agent_id: str = ""
    agent_name: str = ""
    input_text: str = ""


@dataclass
class AgentExecutionCompletedEvent(DomainEvent):
    """Fired when an agent completes execution."""

    agent_id: str = ""
    steps: int = 0
    total_tokens: int = 0
    duration_ms: int = 0


# --- Workflow Events ---

@dataclass
class WorkflowExecutionStartedEvent(DomainEvent):
    """Fired when a workflow execution begins."""

    workflow_id: str = ""
    execution_id: str = ""


@dataclass
class WorkflowExecutionCompletedEvent(DomainEvent):
    """Fired when a workflow execution completes."""

    workflow_id: str = ""
    execution_id: str = ""
    status: str = ""
    duration_ms: int = 0


# --- Knowledge Base Events ---

@dataclass
class KnowledgeBaseCreatedEvent(DomainEvent):
    """Fired when a knowledge base is created."""

    kb_id: str = ""
    name: str = ""
    embedding_model: str = ""


# --- Provider Events ---

@dataclass
class ProviderAddedEvent(DomainEvent):
    """Fired when a new model provider is added."""

    provider_id: str = ""
    provider_name: str = ""


# =============================================================================
# Event Bus
# =============================================================================


EventHandler = Callable[[DomainEvent], Coroutine[Any, Any, None]]


class EventBus:
    """
    Async event bus for domain events.

    Usage:
        bus = get_event_bus()

        # Register handler
        @bus.on(DocumentUploadedEvent)
        async def handle_upload(event: DocumentUploadedEvent):
            await process_document(event.document_id)

        # Emit event
        await bus.emit(DocumentUploadedEvent(
            document_id="doc-123",
            kb_id="kb-456",
            filename="report.pdf",
        ))
    """

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[EventHandler]] = {}

    def on(self, event_type: type[DomainEvent]) -> Callable:
        """Decorator to register an event handler."""

        def decorator(func: EventHandler) -> EventHandler:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(func)
            logger.debug("Event handler registered", event_type=event_type.__name__)
            return func

        return decorator

    def register(self, event_type: type[DomainEvent], handler: EventHandler) -> None:
        """Register an event handler (non-decorator form)."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def emit(self, event: DomainEvent) -> None:
        """
        Emit an event to all registered handlers.

        Handlers are executed concurrently. Failures in one handler
        do not affect others.
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])

        # Also check for base DomainEvent handlers (catch-all)
        catch_all = self._handlers.get(DomainEvent, [])
        all_handlers = handlers + catch_all

        if not all_handlers:
            logger.debug("No handlers for event", event_type=event_type.__name__)
            return

        logger.info(
            "Event emitted",
            event_type=event_type.__name__,
            event_id=event.event_id,
            handlers=len(all_handlers),
        )

        # Execute handlers concurrently
        tasks = [
            asyncio.create_task(self._safe_execute(handler, event))
            for handler in all_handlers
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def emit_background(self, event: DomainEvent) -> None:
        """
        Emit an event without waiting for handlers to complete.

        Fire-and-forget — handlers run in the background.
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        catch_all = self._handlers.get(DomainEvent, [])
        all_handlers = handlers + catch_all

        for handler in all_handlers:
            asyncio.create_task(self._safe_execute(handler, event))

    async def _safe_execute(self, handler: EventHandler, event: DomainEvent) -> None:
        """Execute a handler, catching and logging any exceptions."""
        try:
            await handler(event)
        except Exception as e:
            logger.error(
                "Event handler failed",
                handler=handler.__name__,
                event_type=type(event).__name__,
                error=str(e),
            )

    def clear(self) -> None:
        """Remove all registered handlers."""
        self._handlers.clear()


# =============================================================================
# Singleton
# =============================================================================

_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get or create the event bus singleton."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
        _register_default_handlers(_event_bus)
    return _event_bus


# =============================================================================
# Default Handlers
# =============================================================================


def _register_default_handlers(bus: EventBus) -> None:
    """Register default event handlers for logging and metrics."""

    @bus.on(DomainEvent)
    async def log_all_events(event: DomainEvent) -> None:
        """Catch-all handler: log every event."""
        logger.info(
            "Domain event",
            event_type=event.event_type,
            event_id=event.event_id,
            tenant_id=event.tenant_id,
        )

    @bus.on(MessageCompletedEvent)
    async def track_llm_usage(event: MessageCompletedEvent) -> None:
        """Track LLM token usage for cost management."""
        try:
            from ai_platform.observability.metrics import LLM_TOKENS_TOTAL

            LLM_TOKENS_TOTAL.labels(model=event.model, direction="input").inc(
                event.prompt_tokens
            )
            LLM_TOKENS_TOTAL.labels(model=event.model, direction="output").inc(
                event.completion_tokens
            )
        except ImportError:
            pass

    @bus.on(DocumentUploadedEvent)
    async def log_document_upload(event: DocumentUploadedEvent) -> None:
        """Log document upload for audit."""
        logger.info(
            "Document uploaded",
            document_id=event.document_id,
            kb_id=event.kb_id,
            filename=event.filename,
            file_size=event.file_size,
        )

    @bus.on(AgentExecutionCompletedEvent)
    async def track_agent_metrics(event: AgentExecutionCompletedEvent) -> None:
        """Track agent execution metrics."""
        try:
            from ai_platform.observability.metrics import (
                AGENT_EXECUTION_STEPS,
                AGENT_EXECUTIONS_TOTAL,
            )

            AGENT_EXECUTIONS_TOTAL.labels(
                agent_id=event.agent_id, status="completed"
            ).inc()
            AGENT_EXECUTION_STEPS.labels(agent_id=event.agent_id).observe(event.steps)
        except ImportError:
            pass
