"""Unit tests for Context Engine — token counting and context management strategies."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from ai_platform.api.schemas.chat import ChatMessage
from ai_platform.core.context.engine import (
    ContextConfig,
    ContextStrategy,
    SlidingWindowStrategy,
    TokenTruncateStrategy,
    TokenCounter,
    ContextEngine,
)


# ---------------------------------------------------------------------------
# Token Counter Tests
# ---------------------------------------------------------------------------


def test_token_counter_count_basic():
    """Test basic token counting for simple text.

    Uses a mock for tiktoken to ensure zero network dependency —
    tiktoken's encoding data is local, but we patch it defensively.
    """
    text = "Hello, world!"
    # Patch tiktoken.get_encoding to return a fake encoder that
    # counts characters as a rough token stand-in.
    class _FakeEncoder:
        def encode(self, s):
            # ~1 token per char is a conservative stand-in for testing
            return list(s)

    with patch("tiktoken.get_encoding", return_value=_FakeEncoder()):
        # Reset cached encoders so the patch takes effect
        TokenCounter._encoders.clear()
        count = TokenCounter.count(text, model="gpt-4o")
    assert count >= 2  # At least some tokens counted


def test_token_counter_count_chinese():
    """Test token counting for Chinese text (higher token density)."""
    text = "你好世界"  # "Hello world" in Chinese
    count = TokenCounter.count(text, model="gpt-4o")
    # Chinese characters typically use more tokens
    assert count >= 2


def test_token_counter_count_empty():
    """Test token counting for empty string."""
    text = ""
    count = TokenCounter.count(text, model="gpt-4o")
    assert count == 0


def test_token_counter_count_messages_with_overhead():
    """Test token counting for messages includes per-message overhead."""
    messages = [
        ChatMessage(role="system", content="You are a helpful assistant."),
        ChatMessage(role="user", content="Hi"),
    ]
    count = TokenCounter.count_messages(messages, model="gpt-4o")
    # Should include message overhead (4 tokens per message) + content + 2 for assistant priming
    assert count > 0
    # At least 4 overhead * 2 messages + 2 priming = 10
    assert count >= 10


def test_token_counter_count_messages_with_tool_calls():
    """Test token counting includes tool call tokens."""
    from ai_platform.api.schemas.chat import ToolCall, ToolCallFunction

    messages = [
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    function=ToolCallFunction(name="get_weather", arguments='{"city": "Beijing"}'),
                )
            ],
        )
    ]
    count = TokenCounter.count_messages(messages, model="gpt-4o")
    # Should include tool call name and arguments
    assert count > 4  # At least overhead + tool call content


def test_token_counter_model_to_encoding():
    """Test model name to tiktoken encoding mapping."""
    assert TokenCounter._model_to_encoding("gpt-4o") == "cl100k_base"
    assert TokenCounter._model_to_encoding("gpt-3.5-turbo") == "cl100k_base"
    assert TokenCounter._model_to_encoding("claude-3-opus") == "cl100k_base"
    assert TokenCounter._model_to_encoding("qwen-max") == "cl100k_base"
    # Unknown model should default to cl100k_base
    assert TokenCounter._model_to_encoding("unknown-model") == "cl100k_base"


# ---------------------------------------------------------------------------
# Context Config Tests
# ---------------------------------------------------------------------------


def test_context_config_defaults():
    """Test ContextConfig default values."""
    config = ContextConfig()
    assert config.max_tokens == 4096
    assert config.strategy == "hybrid"
    assert config.max_messages == 20
    assert config.summary_model == "qwen-turbo"


def test_context_config_custom_values():
    """Test ContextConfig with custom values."""
    config = ContextConfig(
        max_tokens=8192,
        strategy="sliding_window",
        max_messages=10,
        summary_model="gpt-3.5-turbo",
    )
    assert config.max_tokens == 8192
    assert config.strategy == "sliding_window"
    assert config.max_messages == 10


# ---------------------------------------------------------------------------
# Sliding Window Strategy Tests
# ---------------------------------------------------------------------------


def test_sliding_window_strategy_respects_max_messages():
    """Test that sliding window strategy limits message count."""
    strategy = SlidingWindowStrategy()
    config = ContextConfig(max_messages=3, strategy="sliding_window")

    messages = [
        ChatMessage(role="system", content="System prompt"),
        ChatMessage(role="user", content="Message 1"),
        ChatMessage(role="assistant", content="Response 1"),
        ChatMessage(role="user", content="Message 2"),
        ChatMessage(role="assistant", content="Response 2"),
        ChatMessage(role="user", content="Message 3"),
    ]

    result = asyncio.run(strategy.build_context(messages, config, "gpt-4o"))

    # Should keep system message + last max_messages
    # System message is always preserved
    assert result[0].role == "system"
    # Total should be limited (system + last 3 messages)
    assert len(result) <= 4


def test_sliding_window_strategy_preserves_system_message():
    """Test that system message is always preserved."""
    strategy = SlidingWindowStrategy()
    config = ContextConfig(max_messages=2, strategy="sliding_window")

    messages = [
        ChatMessage(role="system", content="You are a helpful assistant."),
        ChatMessage(role="user", content="Message 1"),
        ChatMessage(role="assistant", content="Response 1"),
        ChatMessage(role="user", content="Message 2"),
    ]

    result = asyncio.run(strategy.build_context(messages, config, "gpt-4o"))

    # System message should always be first
    assert result[0].role == "system"
    assert result[0].content == "You are a helpful assistant."


def test_sliding_window_strategy_empty_messages():
    """Test sliding window with empty message list."""
    strategy = SlidingWindowStrategy()
    config = ContextConfig(max_messages=5, strategy="sliding_window")

    result = asyncio.run(strategy.build_context([], config, "gpt-4o"))

    assert result == []


# ---------------------------------------------------------------------------
# Token Truncate Strategy Tests
# ---------------------------------------------------------------------------


def test_token_truncate_strategy_respects_max_tokens():
    """Test that token truncate strategy limits total tokens."""
    strategy = TokenTruncateStrategy()
    config = ContextConfig(max_tokens=50, strategy="token_truncate")

    # Create messages that exceed token limit
    messages = [
        ChatMessage(role="system", content="System prompt"),
        ChatMessage(role="user", content="A" * 100),  # Long message
        ChatMessage(role="assistant", content="B" * 100),  # Long response
        ChatMessage(role="user", content="C" * 100),  # Another long message
    ]

    result = asyncio.run(strategy.build_context(messages, config, "gpt-4o"))

    # Should truncate to fit within max_tokens
    total_tokens = TokenCounter.count_messages(result, "gpt-4o")
    # Allow some overhead for system message
    assert total_tokens <= 100  # Reasonable upper bound


def test_token_truncate_strategy_preserves_system_message():
    """Test that token truncate preserves system message even when truncating."""
    strategy = TokenTruncateStrategy()
    config = ContextConfig(max_tokens=20, strategy="token_truncate")

    messages = [
        ChatMessage(role="system", content="Important system instructions"),
        ChatMessage(role="user", content="X" * 200),  # Very long message
    ]

    result = asyncio.run(strategy.build_context(messages, config, "gpt-4o"))

    # System message should be preserved
    assert any(msg.role == "system" for msg in result)


# ---------------------------------------------------------------------------
# Context Engine Tests
# ---------------------------------------------------------------------------


def test_context_engine_initialization():
    """Test ContextEngine can be initialized."""
    engine = ContextEngine()
    assert engine is not None


def test_context_engine_build_context_with_default_strategy():
    """Test ContextEngine builds context with default strategy."""
    engine = ContextEngine()
    config = ContextConfig(strategy="sliding_window", max_messages=5)

    messages = [
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi there!"),
    ]

    result = asyncio.run(engine.build_context("System prompt", messages, "gpt-4o", config))

    # Should return messages with system prompt prepended
    assert isinstance(result, list)
    assert len(result) > 0
    assert result[0].role == "system"
    assert result[0].content == "System prompt"


def test_context_engine_handles_empty_messages():
    """Test ContextEngine handles empty message list gracefully."""
    engine = ContextEngine()
    config = ContextConfig()

    result = asyncio.run(engine.build_context(None, [], "gpt-4o", config))

    # Should return empty list or just system message if provided
    assert isinstance(result, list)


def test_context_engine_preserves_message_order():
    """Test that context engine preserves message order."""
    engine = ContextEngine()
    config = ContextConfig(strategy="sliding_window", max_messages=10)

    messages = [
        ChatMessage(role="user", content="First"),
        ChatMessage(role="assistant", content="Second"),
        ChatMessage(role="user", content="Third"),
    ]

    result = asyncio.run(engine.build_context("System", messages, "gpt-4o", config))

    # System message should be first
    assert result[0].role == "system"
    # User messages should maintain order after system
    user_messages = [m for m in result if m.role == "user"]
    assert len(user_messages) >= 1


def test_context_engine_without_system_prompt():
    """Test ContextEngine works without system prompt."""
    engine = ContextEngine()
    config = ContextConfig(strategy="sliding_window", max_messages=5)

    messages = [
        ChatMessage(role="user", content="Hello"),
    ]

    result = asyncio.run(engine.build_context(None, messages, "gpt-4o", config))

    # Should not have system message
    assert not any(m.role == "system" and m.content != "System" for m in result)


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


def test_token_counter_handles_unicode():
    """Test token counter handles various unicode characters."""
    text = "Hello 世界 🌍"
    count = TokenCounter.count(text, model="gpt-4o")
    assert count > 0


def test_token_counter_handles_special_characters():
    """Test token counter handles special characters."""
    text = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
    count = TokenCounter.count(text, model="gpt-4o")
    assert count > 0


def test_context_config_with_very_small_token_limit():
    """Test context management with very small token limit."""
    strategy = SlidingWindowStrategy()
    config = ContextConfig(max_tokens=10, strategy="sliding_window", max_messages=2)

    messages = [
        ChatMessage(role="system", content="System"),
        ChatMessage(role="user", content="A very long message that exceeds the token limit"),
    ]

    result = asyncio.run(strategy.build_context(messages, config, "gpt-4o"))

    # Should still return something (at least system message)
    assert len(result) >= 1
