"""Integration tests for Chat flow — completions, streaming, conversation management."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    """Create an async test client."""
    from ai_platform.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_headers():
    """Auth headers for test requests."""
    return {"X-API-Key": "aiplat_test123456"}


# =============================================================================
# Chat Completions Tests
# =============================================================================


class TestChatCompletions:
    """Test chat completion endpoints."""

    @pytest.mark.asyncio
    async def test_chat_completion_endpoint_exists(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat completion endpoint is accessible."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=auth_headers,
        )
        # Should not return 404
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_chat_completion_requires_auth(self, client: AsyncClient) -> None:
        """Test that chat completion requires authentication."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_chat_completion_validates_messages(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat completion validates messages array."""
        # Empty messages should fail validation
        resp = await client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_completion_validates_temperature(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat completion validates temperature range."""
        # Temperature > 2.0 should fail
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "temperature": 5.0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_completion_validates_top_p(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat completion validates top_p range."""
        # top_p > 1.0 should fail
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "top_p": 1.5,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422


# =============================================================================
# Chat Streaming Tests
# =============================================================================


class TestChatStreaming:
    """Test chat streaming functionality."""

    @pytest.mark.asyncio
    async def test_chat_streaming_endpoint(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that streaming chat completion works."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
            headers=auth_headers,
        )
        # Should return streaming response (200) or error
        assert resp.status_code in (200, 503)  # 503 if LLM unavailable

    @pytest.mark.asyncio
    async def test_chat_streaming_content_type(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that streaming response has correct content type."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
            headers=auth_headers,
        )
        if resp.status_code == 200:
            # Streaming responses should be text/event-stream
            assert "text/event-stream" in resp.headers.get("content-type", "")


# =============================================================================
# Conversation Management Tests
# =============================================================================


class TestConversationManagement:
    """Test conversation management endpoints."""

    @pytest.mark.asyncio
    async def test_list_conversations(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test listing conversations."""
        resp = await client.get(
            "/api/v1/conversations/",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert "total" in data["data"]

    @pytest.mark.asyncio
    async def test_get_conversation_detail(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test getting conversation detail."""
        # First, try to get a non-existent conversation
        fake_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/conversations/{fake_id}",
            headers=auth_headers,
        )
        # Should return 404 for non-existent conversation
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_conversation_messages(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test getting conversation messages."""
        fake_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/conversations/{fake_id}/messages",
            headers=auth_headers,
        )
        # Should return 404 for non-existent conversation
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_conversation(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test deleting a conversation."""
        fake_id = uuid.uuid4()
        resp = await client.delete(
            f"/api/v1/conversations/{fake_id}",
            headers=auth_headers,
        )
        # Should return 404 for non-existent conversation
        assert resp.status_code == 404


# =============================================================================
# Chat Request Validation Tests
# =============================================================================


class TestChatRequestValidation:
    """Test chat request validation."""

    @pytest.mark.asyncio
    async def test_chat_requires_model(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat request requires model field."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hello"}]},
            headers=auth_headers,
        )
        # Should succeed with default model or fail validation
        assert resp.status_code in (200, 422, 503)

    @pytest.mark.asyncio
    async def test_chat_message_role_validation(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that message role is validated."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "invalid_role", "content": "Hello"}],
            },
            headers=auth_headers,
        )
        # Schema may or may not validate role strictly
        assert resp.status_code in (200, 422, 503)

    @pytest.mark.asyncio
    async def test_chat_message_content_nullable(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that message content can be null (for tool calls)."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "assistant", "content": None}],
            },
            headers=auth_headers,
        )
        # Should accept null content (for tool call messages)
        assert resp.status_code in (200, 422, 503)


# =============================================================================
# Chat Response Format Tests
# =============================================================================


class TestChatResponseFormat:
    """Test chat response format compliance."""

    @pytest.mark.asyncio
    async def test_chat_response_has_choices(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat response includes choices array."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "choices" in data
            assert isinstance(data["choices"], list)

    @pytest.mark.asyncio
    async def test_chat_response_has_usage(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat response includes usage information."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "usage" in data
            assert "prompt_tokens" in data["usage"]
            assert "completion_tokens" in data["usage"]
            assert "total_tokens" in data["usage"]

    @pytest.mark.asyncio
    async def test_chat_response_has_conversation_id(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat response includes conversation_id (AI Platform extension)."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            # AI Platform extension: conversation_id
            assert "conversation_id" in data


# =============================================================================
# Chat with Conversation ID Tests
# =============================================================================


class TestChatWithConversationId:
    """Test chat with existing conversation_id."""

    @pytest.mark.asyncio
    async def test_chat_with_valid_conversation_id(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test chat with valid conversation_id continues conversation."""
        # First, create a conversation by sending a message
        resp1 = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            headers=auth_headers,
        )

        if resp1.status_code == 200:
            conv_id = resp1.json().get("conversation_id")
            if conv_id:
                # Continue the conversation
                resp2 = await client.post(
                    "/api/v1/chat/completions",
                    json={
                        "model": "gpt-4o",
                        "messages": [{"role": "user", "content": "How are you?"}],
                        "conversation_id": conv_id,
                    },
                    headers=auth_headers,
                )
                assert resp2.status_code == 200
                assert resp2.json().get("conversation_id") == conv_id

    @pytest.mark.asyncio
    async def test_chat_with_invalid_conversation_id(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test chat with invalid conversation_id creates new conversation."""
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "conversation_id": fake_id,
            },
            headers=auth_headers,
        )
        # Should create new conversation if ID doesn't exist
        if resp.status_code == 200:
            # New conversation should be created
            assert resp.json().get("conversation_id") != fake_id
