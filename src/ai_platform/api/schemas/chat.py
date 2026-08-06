"""Chat API schemas — request and response models."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pydantic import BaseModel, Field, StrictStr, field_validator


# =============================================================================
# Message Types
# =============================================================================


class ToolCallFunction(BaseModel):
    name: StrictStr = Field(max_length=256)
    arguments: StrictStr = Field(max_length=10000)  # JSON string


class ToolCall(BaseModel):
    id: StrictStr = Field(max_length=256)
    type: StrictStr = Field(default="function", max_length=64)
    function: ToolCallFunction


class ChatMessage(BaseModel):
    role: StrictStr = Field(description="system | user | assistant | tool", max_length=32)
    content: StrictStr | None = Field(default=None, max_length=100000)
    tool_calls: list[ToolCall] | None = None
    tool_call_id: StrictStr | None = Field(default=None, max_length=256)
    name: StrictStr | None = Field(default=None, max_length=256)

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str | None) -> str | None:
        """Validate content field - check for null bytes and other injection attempts."""
        if v is None:
            return v
        # Reject null bytes (common injection vector)
        if "\x00" in v:
            raise ValueError("Content contains null bytes")
        return v


# =============================================================================
# Request
# =============================================================================


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request."""

    model: StrictStr = Field(default="gpt-4o", max_length=100, description="Model name configured in LiteLLM")
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=100000)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    stream: bool = Field(default=False)
    stop: list[StrictStr] | StrictStr | None = Field(default=None, max_length=4)
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    tools: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    tool_choice: str | dict | None = None

    # AI Platform extensions
    conversation_id: uuid.UUID | None = Field(default=None, description="Continue an existing conversation")
    user_id: StrictStr | None = Field(default=None, max_length=256, description="External user identifier")

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        """Validate model name - only allow alphanumeric, dash, underscore, dot."""
        if not re.match(r"^[a-zA-Z0-9._-]+$", v):
            raise ValueError("Model name contains invalid characters")
        return v

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str | None) -> str | None:
        """Validate user_id - whitelist allows only alphanumeric, dash, underscore, dot.

        Upgraded from a blacklist (weak) to a whitelist pattern consistent with the
        ``model`` field validator. This prevents SQL injection, path traversal, and
        other injection vectors by rejecting anything outside the safe character set.
        """
        if v is None:
            return v
        if not re.match(r"^[a-zA-Z0-9._-]+$", v):
            raise ValueError("user_id contains invalid characters")
        return v


# =============================================================================
# Response (non-streaming)
# =============================================================================


class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: UsageInfo
    # AI Platform extensions
    conversation_id: uuid.UUID | None = None


# =============================================================================
# Streaming Response
# =============================================================================


class DeltaMessage(BaseModel):
    role: str | None = None
    content: str | None = None
    tool_calls: list[dict] | None = None


class StreamChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: str | None = None


class ChatCompletionStreamChunk(BaseModel):
    """OpenAI-compatible streaming chunk."""

    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[StreamChoice]
    usage: UsageInfo | None = None
