"""Global error handling middleware."""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request
from fastapi.responses import ORJSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ai_platform.api.exceptions import AppError

logger = structlog.get_logger()


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    Global error handler — catches all exceptions and returns standardized responses.

    Response format:
    {
        "code": 50001,
        "error": "MODEL_TIMEOUT",
        "message": "Model request timed out",
        "detail": null,
        "trace_id": "abc-123",
        "timestamp": "2026-07-31T10:00:00Z"
    }
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        start = time.perf_counter()

        try:
            response = await call_next(request)
            return response

        except AppError as e:
            # Known application errors — return appropriate status code
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "App error",
                error_code=e.error_code,
                status=e.status_code,
                message=e.message,
                trace_id=trace_id,
                elapsed_ms=round(elapsed_ms, 1),
                path=str(request.url.path),
            )
            return ORJSONResponse(
                status_code=e.status_code,
                content={
                    "code": e.status_code,
                    "error": e.error_code,
                    "message": e.message,
                    "detail": e.detail,
                    "trace_id": trace_id,
                },
            )

        except Exception as e:
            # Unexpected errors — always return 500, never leak internals
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "Unhandled exception",
                error=str(e),
                error_type=type(e).__name__,
                trace_id=trace_id,
                elapsed_ms=round(elapsed_ms, 1),
                path=str(request.url.path),
                exc_info=True,
            )
            return ORJSONResponse(
                status_code=500,
                content={
                    "code": 500,
                    "error": "INTERNAL_ERROR",
                    "message": "An internal error occurred",
                    "detail": None,
                    "trace_id": trace_id,
                },
            )
