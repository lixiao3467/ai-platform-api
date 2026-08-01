"""Fallback chain — try alternative models when primary fails."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from ai_platform.core.model_router.circuit_breaker import (
    CircuitOpenError,
    get_circuit_breaker,
)

logger = structlog.get_logger()


@dataclass
class FallbackEntry:
    """A single entry in the fallback chain."""

    model: str
    provider: str  # Maps to circuit breaker name
    priority: int = 0


class FallbackChain:
    """
    Ordered fallback chain for model selection.

    When the primary model fails (circuit open or call error),
    automatically tries the next model in the chain.

    Example chain:
        qwen-max → deepseek-chat → gpt-4o-mini
    """

    def __init__(self, entries: list[FallbackEntry] | None = None) -> None:
        self._entries = sorted(entries or [], key=lambda e: e.priority, reverse=True)

    @property
    def models(self) -> list[str]:
        return [e.model for e in self._entries]

    def add(self, model: str, provider: str, priority: int = 0) -> None:
        """Add a model to the fallback chain."""
        self._entries.append(FallbackEntry(model=model, provider=provider, priority=priority))
        self._entries.sort(key=lambda e: e.priority, reverse=True)

    async def execute(self, func, model_name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        """
        Execute with fallback.

        1. Try the requested model first
        2. If its circuit is open or the call fails, try the next in chain
        3. If all fail, raise the last exception
        """
        # Build execution order: requested model first, then fallbacks
        ordered = self._build_execution_order(model_name)
        last_error: Exception | None = None

        for entry in ordered:
            breaker = get_circuit_breaker(entry.provider)

            if not breaker.is_available:
                logger.info(
                    "Skipping model (circuit open)",
                    model=entry.model,
                    provider=entry.provider,
                )
                continue

            try:
                logger.debug("Trying model", model=entry.model, provider=entry.provider)
                # Override the model in kwargs
                call_kwargs = {**kwargs, "_model_override": entry.model}
                result = await breaker.call(func, *args, **call_kwargs)
                return result
            except CircuitOpenError as e:
                logger.info(
                    "Model circuit open, trying fallback",
                    model=entry.model,
                    error=str(e),
                )
                last_error = e
            except Exception as e:
                logger.warning(
                    "Model call failed, trying fallback",
                    model=entry.model,
                    provider=entry.provider,
                    error=str(e),
                )
                last_error = e

        # All models in the chain failed
        error_msg = (
            f"All models in fallback chain failed. "
            f"Tried: {[e.model for e in ordered]}. "
            f"Last error: {last_error}"
        )
        logger.error("Fallback chain exhausted", error=error_msg)
        raise FallbackExhaustedError(error_msg) from last_error

    def _build_execution_order(self, requested_model: str) -> list[FallbackEntry]:
        """Build execution order: requested model first, then fallbacks by priority."""
        # Find requested model in chain
        requested = None
        others = []

        for entry in self._entries:
            if entry.model == requested_model:
                requested = entry
            else:
                others.append(entry)

        if requested:
            return [requested] + others

        # Requested model not in chain — try it first, then fallbacks
        unknown = FallbackEntry(
            model=requested_model,
            provider=requested_model.split("/")[0] if "/" in requested_model else "unknown",
            priority=999,
        )
        return [unknown] + others


class FallbackExhaustedError(Exception):
    """Raised when all models in the fallback chain have failed."""

    pass
