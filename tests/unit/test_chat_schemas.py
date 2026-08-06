"""Tests for chat API schemas."""

import uuid

import pytest
from pydantic import ValidationError

from ai_platform.api.schemas.chat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamChunk,
    ChatMessage,
    Choice,
    ChoiceMessage,
    DeltaMessage,
    StreamChoice,
    UsageInfo,
)


def test_chat_message_valid() -> None:
    msg = ChatMessage(role="user", content="Hello")
    assert msg.role == "user"
    assert msg.content == "Hello"


def test_chat_message_roles() -> None:
    for role in ("system", "user", "assistant", "tool"):
        msg = ChatMessage(role=role, content="test")
        assert msg.role == role


def test_chat_completion_request_valid() -> None:
    req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="Hi")],
    )
    assert req.model == "gpt-4o"
    assert req.temperature == 0.7
    assert req.stream is False
    assert len(req.messages) == 1


def test_chat_completion_request_empty_messages_rejected() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionRequest(model="gpt-4o", messages=[])


def test_chat_completion_request_temperature_bounds() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=3.0,
        )


def test_chat_completion_request_with_conversation_id() -> None:
    conv_id = uuid.uuid4()
    req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="Hi")],
        conversation_id=conv_id,
    )
    assert req.conversation_id == conv_id


def test_chat_completion_response_serialization() -> None:
    resp = ChatCompletionResponse(
        id="chatcmpl-test",
        created=1000000,
        model="gpt-4o",
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ],
        usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    data = resp.model_dump()
    assert data["choices"][0]["message"]["content"] == "Hello!"
    assert data["usage"]["total_tokens"] == 15


def test_stream_chunk_serialization() -> None:
    chunk = ChatCompletionStreamChunk(
        id="chatcmpl-stream",
        created=1000000,
        model="gpt-4o",
        choices=[
            StreamChoice(
                index=0,
                delta=DeltaMessage(content="Hi"),
                finish_reason=None,
            )
        ],
    )
    json_str = chunk.model_dump_json(exclude_none=True)
    assert '"content":"Hi"' in json_str
    assert "chat.completion.chunk" in json_str


def test_tool_calls_in_message() -> None:
    from ai_platform.api.schemas.chat import ToolCall, ToolCallFunction

    msg = ChatMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id="call_123",
                type="function",
                function=ToolCallFunction(
                    name="http_request",
                    arguments='{"url": "https://api.example.com"}',
                ),
            )
        ],
    )
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].function.name == "http_request"


# ---------------------------------------------------------------------------
# user_id whitelist validation
# ---------------------------------------------------------------------------


def test_user_id_valid_characters_accepted() -> None:
    """user_id allows alphanumeric, dash, underscore, dot."""
    for uid in ("alice", "user-1", "user.name", "user_123", "A.B-C_1"):
        req = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Hi")],
            user_id=uid,
        )
        assert req.user_id == uid


def test_user_id_none_accepted() -> None:
    req = ChatCompletionRequest(
        model="gpt-4o",
        messages=[ChatMessage(role="user", content="Hi")],
        user_id=None,
    )
    assert req.user_id is None


def test_user_id_sql_injection_rejected() -> None:
    """Blacklisted SQL injection patterns must be rejected."""
    for uid in ("alice' OR 1=1--", "user; DROP TABLE", "admin'--", 'x" OR ""="'):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                user_id=uid,
            )


def test_user_id_path_traversal_rejected() -> None:
    for uid in ("../etc/passwd", "admin/../../root", "a\\b"):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                user_id=uid,
            )


def test_user_id_space_and_special_chars_rejected() -> None:
    for uid in ("user name", "user@name", "user!", "name=value"):
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                user_id=uid,
            )
