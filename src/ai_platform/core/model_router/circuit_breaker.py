"""Circuit breaker — prevents cascading failures on LLM provider calls."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum

import structlog

logger = structlog.get_logger()


class CircuitState(Enum):
    CLOSED = "closed"          # Normal — requests pass through
    OPEN = "open"              # Tripped — requests fail immediately
    HALF_OPEN = "half_open"    # Probing — allow one test request


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""

    failure_threshold: int = 5          # Failures before opening
    recovery_timeout: float = 30.0      # Seconds before half-open probe
    success_threshold: int = 2          # Successes in half-open to close
    half_open_max_calls: int = 1        # Concurrent probes allowed


@dataclass
class CircuitStats:
    """Runtime statistics for a circuit breaker."""

    total_calls: int = 0
    total_failures: int = 0
    total_successes: int = 0
    total_rejected: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    state_changed_at: float = field(default_factory=time.time)


class CircuitBreaker:
    """
    Per-provider circuit breaker with three states.

    CLOSED → (failures >= threshold) → OPEN
    OPEN   → (recovery_timeout elapsed) → HALF_OPEN
    HALF_OPEN → (success >= threshold) → CLOSED
    HALF_OPEN → (failure) → OPEN
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self._name = name
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._consecutive_failures = 0
        self._half_open_successes = 0
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def stats(self) -> CircuitStats:
        return self._stats

    @property
    def is_available(self) -> bool:
        """Whether the circuit allows requests through."""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.HALF_OPEN:
            return True
        # OPEN — check if recovery timeout has elapsed
        if time.time() - self._stats.state_changed_at >= self._config.recovery_timeout:
            return True  # Will transition to HALF_OPEN on next call
        return False

    async def call(self, func, *args, **kwargs):  # type: ignore[no-untyped-def]
        """
        Execute a function through the circuit breaker.

        Raises CircuitOpenError if the circuit is OPEN.
        """
        async with self._lock:
            if not self._is_call_allowed():
                self._stats.total_rejected += 1
                raise CircuitOpenError(
                    f"Circuit '{self._name}' is OPEN "
                    f"({self._consecutive_failures} consecutive failures)"
                )

        self._stats.total_calls += 1

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure(e)
            raise

    def _is_call_allowed(self) -> bool:
        """Check if a call is allowed based on current state."""
        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._stats.state_changed_at
            if elapsed >= self._config.recovery_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
                return True
            return False

        # HALF_OPEN — allow limited probes
        return self._stats.total_calls <= self._config.half_open_max_calls

    async def _on_success(self) -> None:
        """Handle a successful call."""
        self._stats.total_successes += 1
        self._stats.last_success_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self._config.success_threshold:
                self._transition_to(CircuitState.CLOSED)
        elif self._state == CircuitState.CLOSED:
            self._consecutive_failures = 0

    async def _on_failure(self, error: Exception) -> None:
        """Handle a failed call."""
        self._stats.total_failures += 1
        self._stats.last_failure_time = time.time()
        self._consecutive_failures += 1

        if self._state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)
        elif self._state == CircuitState.CLOSED:
            if self._consecutive_failures >= self._config.failure_threshold:
                self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._stats.state_changed_at = time.time()

        if new_state == CircuitState.CLOSED:
            self._consecutive_failures = 0
            self._half_open_successes = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_successes = 0

        logger.info(
            "Circuit breaker state change",
            name=self._name,
            from_state=old_state.value,
            to_state=new_state.value,
            consecutive_failures=self._consecutive_failures,
        )

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_successes = 0
        self._stats = CircuitStats()


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is open and rejects a call."""

    pass


# =============================================================================
# Registry — one circuit breaker per provider
# =============================================================================

_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    provider_name: str,
    config: CircuitBreakerConfig | None = None,
) -> CircuitBreaker:
    """Get or create a circuit breaker for a provider."""
    if provider_name not in _breakers:
        _breakers[provider_name] = CircuitBreaker(provider_name, config)
    return _breakers[provider_name]


def get_all_breaker_states() -> dict[str, str]:
    """Get current state of all circuit breakers (for metrics/health)."""
    return {name: b.state.value for name, b in _breakers.items()}
