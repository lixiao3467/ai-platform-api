"""FastAPI application entry point."""

from __future__ import annotations

import logging
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

    # --- Fail fast on insecure defaults in staging/production ---
    from ai_platform.api.startup import validate_secrets

    validate_secrets(settings)

    logger.info(
        "Starting AI Platform",
        env=settings.app_env,
        version=app.version,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
    )

    # --- Startup: verify DB connectivity (schema managed by Alembic) ---
    from ai_platform.infra.database.connection import init_db

    try:
        await init_db()
        logger.info("Database connection verified (schema managed by Alembic)")
    except Exception as e:
        logger.error("Database init failed", error=str(e))
        if settings.is_production:
            raise  # Let orchestration restart the pod

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
        title="AI Platform — 企业级 AI 中台",
        description="""
## 概述

AI Platform 是面向企业的 AI 能力中台，为 10-50 个内部业务系统提供标准化 AI 能力。

## 核心功能

| 模块 | 端点前缀 | 说明 |
|------|----------|------|
| 💬 对话服务 | `/api/v1/chat` | 多轮对话，SSE 流式输出 |
| 📚 知识问答 | `/api/v1/knowledge-bases` | RAG 检索增强生成 |
| 🤖 Agent | `/api/v1/agents` | ReAct 智能体，工具调用 |
| ⚡ 工作流 | `/api/v1/workflows` | DAG 编排，状态持久化 |
| 📝 提示词 | `/api/v1/prompts` | 模板管理，版本控制 |
| 💰 成本管理 | `/api/v1/costs` | 用量归因，预算告警 |
| 📊 评估系统 | `/api/v1/evaluations` | LLM-as-Judge 自动评估 |
| 🔍 审计日志 | `/api/v1/audit-logs` | 全操作审计，多维查询 |
| 🔑 API Key | `/api/v1/api-keys` | 应用级密钥管理 |
| 🔐 SSO | `/api/v1/sso` | 单点登录（OIDC/SAML/飞书/钉钉） |
| 📈 指标查询 | `/api/v1/metrics` | 系统/API/模型指标 |

## 认证方式

所有 API 需要认证，支持两种方式：

1. **JWT Bearer Token**: `Authorization: Bearer <token>` — 用于用户登录
2. **API Key**: `X-API-Key: <key>` — 用于应用间集成

## 错误码说明

| HTTP 状态码 | 含义 |
|-------------|------|
| 400 | 请求参数错误 |
| 401 | 未认证或 Token 已过期 |
| 403 | 权限不足 |
| 404 | 资源不存在 |
| 409 | 资源冲突（如重复创建） |
| 422 | 请求体格式错误 |
| 429 | 请求过于频繁（触发限流） |
| 500 | 服务端内部错误 |
| 501 | 功能尚未实现 |

## 分页约定

列表接口统一使用以下分页参数：
- `page`: 页码（从 1 开始）
- `page_size`: 每页条数（默认 20，最大 100）

响应格式：`{"items": [...], "total": N, "page": P, "page_size": S}`
        """,
        version="0.2.0",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        contact={
            "name": "AI Platform Team",
            "email": "ai-platform@example.com",
        },
        license_info={
            "name": "MIT",
        },
        tags=[
            {"name": "chat", "description": "对话服务 — 多轮对话、SSE 流式生成"},
            {"name": "conversations", "description": "会话管理 — 列表、详情、消息、导出"},
            {"name": "knowledge", "description": "知识库 — RAG 检索增强生成"},
            {"name": "agents", "description": "智能体 — ReAct 模式，工具调用"},
            {"name": "models", "description": "模型提供者 — 配置与管理"},
            {"name": "workflows", "description": "工作流 — DAG 编排与执行"},
            {"name": "prompts", "description": "提示词 — 模板管理与版本控制"},
            {"name": "costs", "description": "成本管理 — 用量统计、预算告警、导出"},
            {"name": "evaluations", "description": "评估系统 — LLM-as-Judge 评估"},
            {"name": "audit-logs", "description": "审计日志 — 全操作审计、多维查询、统计"},
            {"name": "api-keys", "description": "API Key 管理 — 创建、启用/禁用、使用统计"},
            {"name": "sso", "description": "SSO 单点登录 — OIDC/SAML/飞书/钉钉/企业微信"},
            {"name": "metrics", "description": "指标查询 — 系统指标、API 指标、模型指标"},
            {"name": "users", "description": "用户管理 — CRUD、密码重置"},
            {"name": "roles", "description": "角色权限 — RBAC 角色与权限管理"},
            {"name": "auth", "description": "认证 — 登录、刷新令牌、登出"},
            {"name": "system", "description": "系统端点 — 健康检查、指标"},
        ],
    )

    # Middleware order matters — added in REVERSE execution order:
    # (last added = outermost = first to execute on request, last on response)
    #
    # Execution order (request → response):
    #   request_context → CORS → ErrorHandler → Security → SizeLimit → Metrics → Audit → [RateLimit] → endpoint

    # 1. Audit logging (innermost — records every API call including errors)
    from ai_platform.api.middleware.audit import AuditMiddleware

    app.add_middleware(AuditMiddleware)

    # 2. Request body size limit (before other processing to reject large payloads early)
    from ai_platform.api.middleware.request_size import RequestSizeLimitMiddleware

    app.add_middleware(RequestSizeLimitMiddleware)

    # 3. Security headers
    from ai_platform.api.middleware.security import SecurityHeadersMiddleware

    app.add_middleware(SecurityHeadersMiddleware)

    # 4. Prometheus metrics
    from ai_platform.observability.metrics_middleware import MetricsMiddleware

    app.add_middleware(MetricsMiddleware)

    # 5. Error handler (catches all downstream errors, returns standardized JSON)
    from ai_platform.api.middleware.error_handler import ErrorHandlerMiddleware

    app.add_middleware(ErrorHandlerMiddleware)

    # 6. CORS — outermost so all responses (including errors) get proper CORS headers
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
        # Production: explicit origins via CORS_ALLOWED_ORIGINS env var.
        # Empty value → allow_origins=[] → all cross-origin requests rejected.
        allowed_origins = settings.cors_origins
        if not allowed_origins:
            logger.warning(
                "CORS_ALLOWED_ORIGINS is empty — all cross-origin requests will be rejected",
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # 7. Rate Limiting (production only)
    if not settings.is_development:
        from ai_platform.api.middleware.rate_limit import RateLimitMiddleware

        app.add_middleware(RateLimitMiddleware)

    # 8. Request context (outermost — timing + trace_id injection, wraps everything)
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        import re
        import time

        # Validate client-supplied trace id (UUID format only); otherwise generate server-side.
        client_trace = request.headers.get("X-Trace-Id")
        if client_trace and re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            client_trace,
            re.I,
        ):
            trace_id = client_trace
        else:
            trace_id = str(uuid.uuid4())
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
