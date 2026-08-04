"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from starlette.responses import Response

from ai_platform.config import get_settings


# =============================================================================
# Structured Logging Configuration
# =============================================================================


def setup_logging() -> None:
    """Configure structlog for JSON structured logging."""
    settings = get_settings()

    # Shared processors
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.is_development:
        # Dev: pretty console output
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(
            colors=True,
            pad_event=35,
        )
    else:
        # Production: JSON output for log aggregation
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to use structlog formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.app_log_level.upper())

    # Suppress noisy loggers
    for noisy in ("httpx", "httpcore", "uvicorn.access", "litellm"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = structlog.get_logger()


# =============================================================================
# Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown."""
    setup_logging()

    settings = get_settings()
    logger.info(
        "Starting AI Platform",
        env=settings.app_env,
        version=app.version,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
    )

    # --- Startup: init DB + auto-create tables ---
    from ai_platform.infra.database.connection import init_db

    try:
        await init_db()
        logger.info("Database initialized, tables ensured")
    except Exception as e:
        logger.error("Database init failed", error=str(e))

    logger.info("Startup complete — accepting traffic")
    yield

    # --- Shutdown ---
    logger.info("Shutting down AI Platform")


# =============================================================================
# Application Factory
# =============================================================================


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title="AI Platform",
        description="Enterprise AI Middle Platform — Unified AI capabilities",
        version="0.1.0",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    # Middleware order matters — added in REVERSE execution order:
    # (last added = outermost = first to execute on request, last on response)
    #
    # Execution order (request → response):
    #   request_context → CORS → ErrorHandler → Metrics → Security → Audit → [RateLimit] → endpoint

    # 1. Audit logging (innermost — records every API call including errors)
    from ai_platform.api.middleware.audit import AuditMiddleware

    app.add_middleware(AuditMiddleware)

    # 2. Security headers
    from ai_platform.api.middleware.security import SecurityHeadersMiddleware

    app.add_middleware(SecurityHeadersMiddleware)

    # 3. Prometheus metrics
    from ai_platform.observability.metrics_middleware import MetricsMiddleware

    app.add_middleware(MetricsMiddleware)

    # 4. Error handler (catches all downstream errors, returns standardized JSON)
    from ai_platform.api.middleware.error_handler import ErrorHandlerMiddleware

    app.add_middleware(ErrorHandlerMiddleware)

    # 5. CORS — outermost so all responses (including errors) get proper CORS headers
    # Note: allow_origins=["*"] is incompatible with allow_credentials=True per CORS spec.
    # In dev, we use allow_origin_regex=".*" which FastAPI translates to per-request Origin echo.
    if settings.is_development:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=".*",  # Matches any origin (echoes Origin header)
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        # Production: explicit origins via env var, or lock down completely
        allowed_origins = [
            o.strip()
            for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
            if o.strip()
        ]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # 6. Rate Limiting (production only)
    if not settings.is_development:
        from ai_platform.api.middleware.rate_limit import RateLimitMiddleware

        app.add_middleware(RateLimitMiddleware)

    # 7. Request context (outermost — timing + trace_id injection, wraps everything)
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        import time

        trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.1f}"
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Request completed",
                method=request.method,
                path=request.url.path,
                status=status_code,
                elapsed_ms=round(elapsed_ms, 1),
            )
            structlog.contextvars.clear_contextvars()

    # --- Liveness probe — instant 200 for infrastructure healthchecks ---
    @app.get("/live", tags=["system"], response_model=None)
    async def liveness():
        return ORJSONResponse(status_code=200, content={"status": "success"})

    # --- Readiness / deep health check (may be slow) ---
    @app.get("/health", tags=["system"], response_model=None)
    async def health() -> dict:
        from ai_platform.observability.startup import deep_health_check

        deps = await deep_health_check()
        all_ok = all(deps.values())

        return {
            "code": 0,
            "data": {
                "status": "ok" if all_ok else "degraded",
                "service": settings.app_name,
                "version": app.version,
                "env": settings.app_env,
                "dependencies": {
                    k: "ok" if v else "degraded" for k, v in deps.items()
                },
            },
            "message": "ok",
        }

    # --- Prometheus metrics endpoint ---
    @app.get("/metrics", tags=["system"], response_class=Response, response_model=None)
    async def metrics():
        from ai_platform.observability.metrics import get_metrics

        return Response(
            content=get_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # --- Register API routes ---
    from ai_platform.api.v1.router import api_router

    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()


def run() -> None:
    """CLI entry point."""
    settings = get_settings()
    uvicorn.run(
        "ai_platform.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.is_development,
        log_level=settings.app_log_level.lower(),
    )
