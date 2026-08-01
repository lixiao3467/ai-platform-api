"""Core enumerations."""

from __future__ import annotations

from enum import StrEnum


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
