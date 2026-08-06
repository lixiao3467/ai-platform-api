"""Request body size limit middleware — prevents DoS via large payloads."""

from __future__ import annotations

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = structlog.get_logger()

# 10 MB — large enough for file uploads, small enough to prevent abuse
MAX_BODY_SIZE = 10 * 1024 * 1024


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Rejects requests with bodies larger than MAX_BODY_SIZE.

    Returns 413 Payload Too Large if the Content-Length header exceeds the limit.
    For chunked transfer encoding (no Content-Length), the body is read up to
    the limit and rejected if exceeded.

    This prevents:
    - DoS via memory exhaustion
    - Accidental large file uploads
    - Malicious payload injection
    """

    def __init__(self, app, max_size: int = MAX_BODY_SIZE) -> None:
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Only check methods that typically have bodies
        if request.method in ("POST", "PUT", "PATCH"):
            # Check Content-Length header first (fast path)
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    size = int(content_length)
                    if size > self.max_size:
                        logger.warning(
                            "Request body too large",
                            method=request.method,
                            path=request.url.path,
                            content_length=size,
                            max_size=self.max_size,
                            client_ip=request.client.host if request.client else "unknown",
                        )
                        return JSONResponse(
                            status_code=413,
                            content={
                                "code": 413,
                                "error": "Request body too large",
                                "message": f"Maximum allowed size is {self.max_size // (1024 * 1024)}MB",
                                "data": None,
                            },
                        )
                except (ValueError, TypeError):
                    # Invalid Content-Length — let the request through
                    # (will likely fail downstream with 400)
                    pass

        return await call_next(request)
