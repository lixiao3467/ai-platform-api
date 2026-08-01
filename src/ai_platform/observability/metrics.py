"""Prometheus metrics — application-level instrumentation."""

from __future__ import annotations

import time

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# =============================================================================
# HTTP Request Metrics
# =============================================================================

HTTP_REQUESTS_TOTAL = Counter(
    "ai_platform_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "ai_platform_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "ai_platform_http_requests_in_progress",
    "HTTP requests currently being processed",
    ["method"],
)

# =============================================================================
# LLM / Model Metrics
# =============================================================================

LLM_REQUESTS_TOTAL = Counter(
    "ai_platform_llm_requests_total",
    "Total LLM API calls",
    ["model", "provider", "status"],
)

LLM_REQUEST_DURATION = Histogram(
    "ai_platform_llm_request_duration_seconds",
    "LLM API call duration in seconds",
    ["model", "provider"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0],
)

LLM_TOKENS_TOTAL = Counter(
    "ai_platform_llm_tokens_total",
    "Total tokens consumed",
    ["model", "direction"],  # direction: input | output
)

LLM_ERRORS_TOTAL = Counter(
    "ai_platform_llm_errors_total",
    "Total LLM errors",
    ["model", "provider", "error_type"],
)

# =============================================================================
# Circuit Breaker Metrics
# =============================================================================

CIRCUIT_BREAKER_STATE = Gauge(
    "ai_platform_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half_open, 2=open)",
    ["provider"],
)

CIRCUIT_BREAKER_FAILURES = Counter(
    "ai_platform_circuit_breaker_failures_total",
    "Total circuit breaker failures",
    ["provider"],
)

# =============================================================================
# Business Metrics
# =============================================================================

ACTIVE_CONVERSATIONS = Gauge(
    "ai_platform_active_conversations",
    "Number of active conversations",
    ["tenant"],
)

KNOWLEDGE_BASE_CHUNKS = Gauge(
    "ai_platform_knowledge_base_chunks_total",
    "Total chunks across all knowledge bases",
    ["tenant"],
)

AGENT_EXECUTIONS_TOTAL = Counter(
    "ai_platform_agent_executions_total",
    "Total agent executions",
    ["agent_id", "status"],
)

AGENT_EXECUTION_STEPS = Histogram(
    "ai_platform_agent_execution_steps",
    "Number of steps per agent execution",
    ["agent_id"],
    buckets=[1, 2, 3, 5, 8, 10, 15, 20, 30, 50],
)

DOCUMENT_INGESTION_TOTAL = Counter(
    "ai_platform_document_ingestion_total",
    "Total documents ingested",
    ["status"],  # success | failed
)

# =============================================================================
# Metrics Endpoint
# =============================================================================


def get_metrics() -> bytes:
    """Generate Prometheus metrics output."""
    return generate_latest()


# =============================================================================
# Helper: Record LLM call metrics
# =============================================================================


def record_llm_call(
    model: str,
    provider: str,
    duration_seconds: float,
    prompt_tokens: int,
    completion_tokens: int,
    success: bool,
    error_type: str | None = None,
) -> None:
    """Record metrics for a single LLM call."""
    status = "success" if success else "error"

    LLM_REQUESTS_TOTAL.labels(model=model, provider=provider, status=status).inc()
    LLM_REQUEST_DURATION.labels(model=model, provider=provider).observe(duration_seconds)

    if success:
        LLM_TOKENS_TOTAL.labels(model=model, direction="input").inc(prompt_tokens)
        LLM_TOKENS_TOTAL.labels(model=model, direction="output").inc(completion_tokens)
    else:
        LLM_ERRORS_TOTAL.labels(
            model=model, provider=provider, error_type=error_type or "unknown"
        ).inc()
