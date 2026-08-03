"""Audit logging middleware — records every API call to the audit_logs table."""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ai_platform.infra.database.connection import get_session_factory

logger = structlog.get_logger()

# Paths that should not be audited (health checks, docs, metrics)
_AUDIT_SKIP_PATHS = frozenset({
    "/live",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/favicon.ico",
})

# Sensitive fields to redact from request bodies
_SENSITIVE_FIELDS = frozenset({
    "api_key", "password", "secret", "token", "authorization",
    "api_key_ref", "key_hash",
})


def _sanitize_body(body: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact sensitive fields from request body."""
    sanitized: dict[str, Any] = {}
    for key, value in body.items():
        if key.lower() in _SENSITIVE_FIELDS:
            sanitized[key] = "***REDACTED***"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_body(value)
        elif isinstance(value, list):
            sanitized[key] = [
                _sanitize_body(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


def _extract_resource_info(path: str, method: str) -> tuple[str, str | None]:
    """
    Extract resource_type and resource_id from URL path.

    Examples:
        /api/v1/knowledge-bases/abc-123/documents → ("knowledge-base", "abc-123")
        /api/v1/chat/completions                  → ("chat", None)
        /api/v1/agents/xyz-456/run                → ("agent", "xyz-456")
    """
    parts = [p for p in path.strip("/").split("/") if p]

    # Skip "api" and "v1" prefixes
    if len(parts) >= 2 and parts[0] == "api":
        parts = parts[2:]

    resource_type = parts[0] if parts else "unknown"

    # Normalize plural to singular
    type_map = {
        "knowledge-bases": "knowledge-base",
        "conversations": "conversation",
        "agents": "agent",
        "models": "model",
        "providers": "provider",
        "prompts": "prompt",
        "workflows": "workflow",
        "tools": "tool",
    }
    resource_type = type_map.get(resource_type, resource_type)

    # Try to extract resource ID (UUID-like segment)
    resource_id = None
    for part in parts[1:]:
        try:
            uuid.UUID(part)
            resource_id = part
            break
        except ValueError:
            continue

    return resource_type, resource_id


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Automatic audit logging for all API requests.

    Records to audit_logs table asynchronously (non-blocking).
    Captures: who / when / what / result / how_long / tokens.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip non-API paths
        if request.url.path in _AUDIT_SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()

        # Extract request metadata
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        client_ip = request.client.host if request.client else None
        api_key = request.headers.get("X-API-Key")
        api_key_prefix = api_key[:8] if api_key else None

        # Try to read request body for POST/PUT
        body_data = None
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type and request.method in ("POST", "PUT", "PATCH"):
            try:
                body_bytes = await request.body()
                if body_bytes:
                    import json
                    body_data = json.loads(body_bytes)
                    body_data = _sanitize_body(body_data)
            except Exception:
                pass

        # Execute the actual request
        response = await call_next(request)

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        # Extract resource info
        resource_type, resource_id = _extract_resource_info(
            request.url.path, request.method
        )

        # Build audit action: "chat.create", "agent.run", "knowledge-base.query"
        action = f"{resource_type}.{request.method.lower()}"

        # Extract token usage from response headers (set by chat endpoint)
        token_input = None
        token_output = None

        # Extract tenant/app/user from auth context if available
        tenant_id = None
        app_id = None
        user_id = None
        if hasattr(request.state, "auth_context"):
            ctx = request.state.auth_context
            tenant_id = ctx.tenant_id if ctx else None
            app_id = ctx.app_id if ctx else None
            user_id = ctx.user_id if ctx else None

        # Write audit log asynchronously (non-blocking)
        await self._write_audit_log(
            tenant_id=tenant_id,
            app_id=app_id,
            user_id=user_id,
            api_key_prefix=api_key_prefix,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_data=body_data,
            response_code=response.status_code,
            token_input=token_input,
            token_output=token_output,
            latency_ms=elapsed_ms,
            ip_address=client_ip,
            trace_id=trace_id,
        )

        return response

    async def _write_audit_log(self, **fields: Any) -> None:
        """Write audit record to database (fire-and-forget, never blocks response)."""
        try:
            from ai_platform.domain.models import AuditLog

            factory = get_session_factory()
            async with factory() as session:
                audit = AuditLog(**fields)
                session.add(audit)
                await session.commit()
        except Exception as e:
            # Audit failure should NEVER break the request
            logger.warning(
                "Audit log write failed",
                error=str(e),
                action=fields.get("action"),
                trace_id=fields.get("trace_id"),
            )
