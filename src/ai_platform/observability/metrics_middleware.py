"""Prometheus metrics middleware — auto-instrument HTTP requests."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ai_platform.observability.metrics import (
    HTTP_REQUESTS_IN_PROGRESS,
    HTTP_REQUESTS_TOTAL,
    HTTP_REQUEST_DURATION,
)

# Paths to skip in metrics (noise)
_METRICS_SKIP_PATHS = frozenset({
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
})


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Automatic Prometheus instrumentation for HTTP requests.

    Tracks:
    - Request count by method + endpoint + status
    - Request duration histogram
    - Requests in progress gauge
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _METRICS_SKIP_PATHS:
            return await call_next(request)

        method = request.method
        # Normalize endpoint: replace UUIDs with {id} for cardinality control
        endpoint = self._normalize_path(request.url.path)

        HTTP_REQUESTS_IN_PROGRESS.labels(method=method).inc()
        start = time.perf_counter()

        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            elapsed = time.perf_counter() - start
            HTTP_REQUESTS_IN_PROGRESS.labels(method=method).dec()
            HTTP_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
            HTTP_REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(elapsed)

        return response

    @staticmethod
    def _normalize_path(path: str) -> str:
        """
        Normalize URL path for Prometheus label cardinality.

        Replace UUIDs and numeric IDs with placeholders:
        /api/v1/conversations/abc-123-def → /api/v1/conversations/{id}
        /api/v1/knowledge-bases/xyz/documents → /api/v1/knowledge-bases/{id}/documents
        """
        import re

        parts = path.split("/")
        normalized = []
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
        )

        for part in parts:
            if uuid_pattern.match(part):
                normalized.append("{id}")
            elif part.isdigit():
                normalized.append("{id}")
            else:
                normalized.append(part)

        return "/".join(normalized)
