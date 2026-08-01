"""Tests for domain enums."""

from ai_platform.domain.enums import (
    AgentStatus,
    AppStatus,
    AppType,
    DocumentStatus,
    ExecutionStatus,
    MessageRole,
    ModelCapability,
    NodeType,
    ProviderName,
    ToolCategory,
)


def test_app_status_values() -> None:
    assert AppStatus.ACTIVE == "active"
    assert AppStatus.INACTIVE == "inactive"


def test_message_role_values() -> None:
    assert MessageRole.SYSTEM == "system"
    assert MessageRole.USER == "user"
    assert MessageRole.ASSISTANT == "assistant"
    assert MessageRole.TOOL == "tool"


def test_document_status_lifecycle() -> None:
    statuses = [DocumentStatus.PENDING, DocumentStatus.PROCESSING, DocumentStatus.READY, DocumentStatus.FAILED]
    assert len(statuses) == 4


def test_node_types_complete() -> None:
    """All 12 workflow node types should be defined."""
    assert len(NodeType) == 12
    assert NodeType.LLM_CALL == "llm_call"
    assert NodeType.HUMAN_IN_LOOP == "human_in_loop"


def test_provider_names() -> None:
    assert ProviderName.OPENAI == "openai"
    assert ProviderName.ANTHROPIC == "anthropic"
    assert ProviderName.QWEN == "qwen"
    assert ProviderName.DEEPSEEK == "deepseek"
    assert ProviderName.OLLAMA == "ollama"


def test_tool_categories() -> None:
    assert ToolCategory.HTTP_API == "http_api"
    assert ToolCategory.DATABASE == "database"
    assert ToolCategory.KNOWLEDGE == "knowledge"
    assert ToolCategory.CODE_EXEC == "code_exec"


def test_model_capabilities() -> None:
    assert ModelCapability.CHAT == "chat"
    assert ModelCapability.VISION == "vision"
    assert ModelCapability.FUNCTION_CALLING == "function_calling"
