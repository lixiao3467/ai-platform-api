"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()

# --- Chat ---
from ai_platform.api.v1 import chat  # noqa: E402

api_router.include_router(chat.router, prefix="/chat", tags=["chat"])

# --- Conversations ---
from ai_platform.api.v1 import conversations  # noqa: E402

api_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])

# --- Knowledge Bases ---
from ai_platform.api.v1 import knowledge  # noqa: E402

api_router.include_router(knowledge.router, prefix="/knowledge-bases", tags=["knowledge"])

# --- Agents ---
from ai_platform.api.v1 import agents  # noqa: E402

api_router.include_router(agents.router, prefix="/agents", tags=["agents"])

# --- Models & Providers ---
from ai_platform.api.v1 import models  # noqa: E402

api_router.include_router(models.router, prefix="/models", tags=["models"])

# --- Workflows ---
from ai_platform.api.v1 import workflows  # noqa: E402

api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])

# --- Prompts ---
from ai_platform.api.v1 import prompts  # noqa: E402

api_router.include_router(prompts.router, prefix="/prompts", tags=["prompts"])

# --- Costs ---
from ai_platform.api.v1 import costs  # noqa: E402

api_router.include_router(costs.router, prefix="/costs", tags=["costs"])

# --- Evaluations ---
from ai_platform.api.v1 import evaluations  # noqa: E402

api_router.include_router(evaluations.router, prefix="/evaluations", tags=["evaluations"])

# --- Audit Logs ---
from ai_platform.api.v1 import audit_logs  # noqa: E402

api_router.include_router(audit_logs.router, prefix="/audit-logs", tags=["audit-logs"])

# --- SSO Providers ---
from ai_platform.api.v1 import sso  # noqa: E402

api_router.include_router(sso.router, prefix="/sso", tags=["sso"])

# --- API Key Management ---
from ai_platform.api.v1 import api_keys  # noqa: E402

api_router.include_router(api_keys.router, prefix="/api-keys", tags=["api-keys"])

# --- Metrics ---
from ai_platform.api.v1 import metrics_api  # noqa: E402

api_router.include_router(metrics_api.router, prefix="/metrics", tags=["metrics"])

# --- Users & Roles (RBAC) ---
from ai_platform.api.v1 import users as users_module  # noqa: E402

api_router.include_router(users_module.users_router, prefix="/users", tags=["users"])
api_router.include_router(users_module.roles_router, prefix="/roles", tags=["roles"])
api_router.include_router(users_module.auth_router, prefix="/auth", tags=["auth"])

# --- Tenant Self-Service ---
from ai_platform.api.v1 import tenant_self  # noqa: E402

api_router.include_router(tenant_self.router, prefix="/tenant", tags=["tenant"])
