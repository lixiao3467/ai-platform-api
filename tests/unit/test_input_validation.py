"""Unit tests for input validation — body size limit, schema validation, SQL injection prevention."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from ai_platform.api.schemas.chat import ChatCompletionRequest, ChatMessage
from ai_platform.api.middleware.request_size import RequestSizeLimitMiddleware, MAX_BODY_SIZE


# =============================================================================
# Body Size Limit Tests
# =============================================================================


class TestBodySizeLimit:
    """Test request body size limit middleware."""

    def test_max_body_size_is_10mb(self) -> None:
        """Test that max body size is 10MB."""
        assert MAX_BODY_SIZE == 10 * 1024 * 1024

    def test_middleware_exists(self) -> None:
        """Test that RequestSizeLimitMiddleware can be instantiated."""
        class FakeApp:
            pass
        middleware = RequestSizeLimitMiddleware(FakeApp())
        assert middleware is not None
        assert middleware.max_size == MAX_BODY_SIZE

    def test_middleware_custom_size(self) -> None:
        """Test that middleware accepts custom max size."""
        class FakeApp:
            pass
        custom_size = 5 * 1024 * 1024
        middleware = RequestSizeLimitMiddleware(FakeApp(), max_size=custom_size)
        assert middleware.max_size == custom_size


# =============================================================================
# Chat Schema Validation Tests
# =============================================================================


class TestChatSchemaValidation:
    """Test chat schema input validation."""

    def test_valid_chat_request(self) -> None:
        """Test that valid chat request passes validation."""
        request = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Hello")],
        )
        assert request.model == "gpt-4o"
        assert len(request.messages) == 1

    def test_model_name_validation_alphanumeric(self) -> None:
        """Test that model name allows alphanumeric characters."""
        request = ChatCompletionRequest(
            model="gpt-4o-mini",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        assert request.model == "gpt-4o-mini"

    def test_model_name_validation_with_dots(self) -> None:
        """Test that model name allows dots."""
        request = ChatCompletionRequest(
            model="qwen2.5-max",
            messages=[ChatMessage(role="user", content="Hi")],
        )
        assert request.model == "qwen2.5-max"

    def test_model_name_rejects_sql_injection(self) -> None:
        """Test that model name rejects SQL injection attempts."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4'; DROP TABLE users; --",
                messages=[ChatMessage(role="user", content="Hi")],
            )

    def test_model_name_rejects_quotes(self) -> None:
        """Test that model name rejects quotes."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model='gpt-4"test',
                messages=[ChatMessage(role="user", content="Hi")],
            )

    def test_model_max_length(self) -> None:
        """Test that model name has max length."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-" + "a" * 200,
                messages=[ChatMessage(role="user", content="Hi")],
            )

    def test_messages_min_length(self) -> None:
        """Test that messages array has min length of 1."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[],
            )

    def test_messages_max_length(self) -> None:
        """Test that messages array has max length of 100."""
        messages = [ChatMessage(role="user", content=f"Message {i}") for i in range(101)]
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=messages,
            )

    def test_temperature_range(self) -> None:
        """Test that temperature is validated to be between 0 and 2."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                temperature=-0.1,
            )
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                temperature=2.1,
            )

    def test_top_p_range(self) -> None:
        """Test that top_p is validated to be between 0 and 1."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                top_p=-0.1,
            )
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                top_p=1.1,
            )

    def test_max_tokens_range(self) -> None:
        """Test that max_tokens is validated."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                max_tokens=0,
            )
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                max_tokens=200000,
            )


# =============================================================================
# Chat Message Validation Tests
# =============================================================================


class TestChatMessageValidation:
    """Test chat message input validation."""

    def test_valid_message(self) -> None:
        """Test that valid message passes validation."""
        msg = ChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_message_content_max_length(self) -> None:
        """Test that message content has max length."""
        long_content = "a" * 200000
        with pytest.raises(ValidationError):
            ChatMessage(role="user", content=long_content)

    def test_message_content_null_bytes_rejected(self) -> None:
        """Test that null bytes in content are rejected."""
        with pytest.raises(ValidationError):
            ChatMessage(role="user", content="Hello\x00World")

    def test_message_role_max_length(self) -> None:
        """Test that message role has max length."""
        with pytest.raises(ValidationError):
            ChatMessage(role="a" * 100, content="Hi")

    def test_message_nullable_content(self) -> None:
        """Test that message content can be None (for tool calls)."""
        msg = ChatMessage(role="assistant", content=None)
        assert msg.content is None


# =============================================================================
# User ID Validation Tests
# =============================================================================


class TestUserIdValidation:
    """Test user_id input validation."""

    def test_valid_user_id(self) -> None:
        """Test that valid user_id passes validation."""
        request = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Hi")],
            user_id="user123",
        )
        assert request.user_id == "user123"

    def test_user_id_rejects_sql_injection(self) -> None:
        """Test that user_id rejects SQL injection attempts."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                user_id="admin' OR '1'='1",
            )

    def test_user_id_rejects_semicolon(self) -> None:
        """Test that user_id rejects semicolons."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                user_id="user; DROP TABLE users",
            )

    def test_user_id_rejects_comment_syntax(self) -> None:
        """Test that user_id rejects characters not in the whitelist.

        With the whitelist `^[a-zA-Z0-9._-]+$`, dashes are allowed (so `user--comment`
        is valid). Instead, we test that characters outside the whitelist — spaces,
        quotes, parentheses — are rejected.
        """
        for bad_uid in ("user name", "user'name", "user(name)", "user{name}"):
            with pytest.raises(ValidationError):
                ChatCompletionRequest(
                    model="gpt-4o",
                    messages=[ChatMessage(role="user", content="Hi")],
                    user_id=bad_uid,
                )

    def test_user_id_max_length(self) -> None:
        """Test that user_id has max length."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                user_id="a" * 300,
            )

    def test_nullable_user_id(self) -> None:
        """Test that user_id can be None."""
        request = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Hi")],
            user_id=None,
        )
        assert request.user_id is None


# =============================================================================
# SQL Injection Prevention Tests
# =============================================================================


class TestSQLInjectionPrevention:
    """Test SQL injection prevention in input validation."""

    def test_model_rejects_union_select(self) -> None:
        """Test that model rejects UNION SELECT."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4 UNION SELECT * FROM users",
                messages=[ChatMessage(role="user", content="Hi")],
            )

    def test_model_rejects_drop_table(self) -> None:
        """Test that model rejects DROP TABLE."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4; DROP TABLE users;",
                messages=[ChatMessage(role="user", content="Hi")],
            )

    def test_user_id_rejects_exec(self) -> None:
        """Test that user_id rejects EXEC keyword."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="gpt-4o",
                messages=[ChatMessage(role="user", content="Hi")],
                user_id="exec xp_cmdshell",
            )

    def test_content_null_byte_injection(self) -> None:
        """Test that content rejects null byte injection."""
        with pytest.raises(ValidationError):
            ChatMessage(
                role="user",
                content="Hello\x00; DROP TABLE users;--",
            )


# =============================================================================
# Tool Call Validation Tests
# =============================================================================


class TestToolCallValidation:
    """Test tool call input validation."""

    def test_valid_tool_call(self) -> None:
        """Test that valid tool call passes validation."""
        from ai_platform.api.schemas.chat import ToolCall, ToolCallFunction

        tool_call = ToolCall(
            id="call_123",
            function=ToolCallFunction(
                name="get_weather",
                arguments='{"city": "Beijing"}',
            ),
        )
        assert tool_call.id == "call_123"

    def test_tool_call_function_name_max_length(self) -> None:
        """Test that tool call function name has max length."""
        from ai_platform.api.schemas.chat import ToolCall, ToolCallFunction

        with pytest.raises(ValidationError):
            ToolCallFunction(
                name="a" * 300,
                arguments="{}",
            )

    def test_tool_call_arguments_max_length(self) -> None:
        """Test that tool call arguments have max length."""
        from ai_platform.api.schemas.chat import ToolCall, ToolCallFunction

        with pytest.raises(ValidationError):
            ToolCallFunction(
                name="test",
                arguments="{" + "a" * 20000 + "}",
            )


# =============================================================================
# Knowledge Base Schema Validation Tests
# =============================================================================


class TestKBSchemaValidation:
    """Test knowledge base schema validation."""

    def test_valid_kb_create_request(self) -> None:
        """Test that valid KB create request passes validation."""
        from ai_platform.api.v1.knowledge import KBCreateRequest

        req = KBCreateRequest(
            name="Test KB",
            description="A test knowledge base",
            embedding_model="text-embedding-3-small",
        )
        assert req.name == "Test KB"

    def test_kb_name_max_length(self) -> None:
        """Test that KB name has max length."""
        from ai_platform.api.v1.knowledge import KBCreateRequest

        with pytest.raises(ValidationError):
            KBCreateRequest(name="a" * 200)

    def test_kb_name_min_length(self) -> None:
        """Test that KB name has min length."""
        from ai_platform.api.v1.knowledge import KBCreateRequest

        with pytest.raises(ValidationError):
            KBCreateRequest(name="")

    def test_kb_description_max_length(self) -> None:
        """Test that KB description has max length."""
        from ai_platform.api.v1.knowledge import KBCreateRequest

        with pytest.raises(ValidationError):
            KBCreateRequest(
                name="Test",
                description="a" * 2000,
            )

    def test_kb_chunk_size_range(self) -> None:
        """Test that chunk_size is validated."""
        from ai_platform.api.v1.knowledge import KBCreateRequest

        with pytest.raises(ValidationError):
            KBCreateRequest(name="Test", chunk_size=50)
        with pytest.raises(ValidationError):
            KBCreateRequest(name="Test", chunk_size=3000)

    def test_kb_query_request_validation(self) -> None:
        """Test KB query request validation."""
        from ai_platform.api.v1.knowledge import KBQueryRequest

        req = KBQueryRequest(question="What is AI?")
        assert req.question == "What is AI?"

    def test_kb_query_question_max_length(self) -> None:
        """Test that question has max length."""
        from ai_platform.api.v1.knowledge import KBQueryRequest

        with pytest.raises(ValidationError):
            KBQueryRequest(question="a" * 20000)


# =============================================================================
# StrictStr Tests
# =============================================================================


class TestStrictStr:
    """Test StrictStr validation (no implicit type coercion)."""

    def test_model_must_be_string(self) -> None:
        """Test that model must be a string (no int coercion)."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model=123,  # type: ignore
                messages=[ChatMessage(role="user", content="Hi")],
            )

    def test_role_must_be_string(self) -> None:
        """Test that role must be a string."""
        with pytest.raises(ValidationError):
            ChatMessage(role=123, content="Hi")  # type: ignore
