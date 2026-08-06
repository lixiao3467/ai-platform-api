"""Core enumerations."""

from __future__ import annotations

from enum import StrEnum


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class PermissionScope(StrEnum):
    PLATFORM = "platform"
    TENANT = "tenant"
    APP = "app"


class Permission(StrEnum):
    """Permission constants for RBAC.

    Format: ``resource.action``
    """

    # ── Platform-level ──────────────────────────────────────────────
    TENANT_MANAGE = "tenant:manage"
    TENANT_VIEW_ALL = "tenant:view_all"
    MODEL_MANAGE = "model:manage"
    SYSTEM_CONFIG = "system:config"
    AUDIT_VIEW_ALL = "audit:view_all"

    # ── Tenant-level ───────────────────────────────────────────────
    TENANT_CONFIG = "tenant:config"
    TENANT_QUOTA_VIEW = "tenant:quota_view"
    APIKEY_MANAGE = "apikey:manage"
    USER_MANAGE = "user:manage"
    APP_MANAGE = "app:manage"
    AGENT_MANAGE = "agent:manage"
    WORKFLOW_MANAGE = "workflow:manage"
    KNOWLEDGE_MANAGE = "knowledge:manage"
    PROMPT_MANAGE = "prompt:manage"
    TOOL_MANAGE = "tool:manage"
    AUDIT_VIEW = "audit:view"

    # ── App-level ──────────────────────────────────────────────────
    CHAT_USE = "chat:use"
    AGENT_EXECUTE = "agent:execute"
    WORKFLOW_EXECUTE = "workflow:execute"
    KNOWLEDGE_QUERY = "knowledge:query"

    # ── Legacy aliases (backward-compatible with existing code) ────
    AGENT_WRITE = "agent.write"
    KNOWLEDGE_WRITE = "knowledge.write"
    WORKFLOW_WRITE = "workflow.write"
    USER_UPDATE = "user.update"
    USER_DELETE = "user.delete"

    # ── Read aliases ───────────────────────────────────────────────
    AGENT_READ = "agent.read"
    KNOWLEDGE_READ = "knowledge.read"
    WORKFLOW_READ = "workflow.read"
    APP_READ = "app.read"
    PROMPT_READ = "prompt.read"
    TOOL_READ = "tool.read"
    MODEL_READ = "model.read"
    COST_READ = "cost.read"
    EVALUATION_READ = "evaluation.read"
    METRIC_READ = "metric.read"


# Map legacy write permissions to new manage permissions for backward compat
LEGACY_PERM_MAP: dict[str, str] = {
    "agent.write": Permission.AGENT_MANAGE,
    "knowledge.write": Permission.KNOWLEDGE_MANAGE,
    "workflow.write": Permission.WORKFLOW_MANAGE,
    "user.update": Permission.USER_MANAGE,
    "user.delete": Permission.USER_MANAGE,
}


class AppStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class AppType(StrEnum):
    CHAT = "chat"
    RAG = "rag"
    AGENT = "agent"
    WORKFLOW = "workflow"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class AgentStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    INACTIVE = "inactive"


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class NodeType(StrEnum):
    START = "start"
    END = "end"
    LLM_CALL = "llm_call"
    RAG_QUERY = "rag_query"
    HTTP_REQUEST = "http_request"
    CODE_EXEC = "code_exec"
    CONDITION = "condition"
    PARALLEL = "parallel"
    MERGE = "merge"
    HUMAN_IN_LOOP = "human_in_loop"
    SUB_WORKFLOW = "sub_workflow"
    DELAY = "delay"


class ToolCategory(StrEnum):
    HTTP_API = "http_api"
    DATABASE = "database"
    KNOWLEDGE = "knowledge"
    CODE_EXEC = "code_exec"
    WORKFLOW = "workflow"
    CUSTOM = "custom"


class ModelCapability(StrEnum):
    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    VISION = "vision"
    FUNCTION_CALLING = "function_calling"


class ProviderName(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    QWEN = "qwen"
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"
    VLLM = "vllm"
