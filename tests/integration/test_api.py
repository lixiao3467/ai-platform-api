"""Integration tests — API end-to-end testing with httpx AsyncClient."""

from __future__ import annotations

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
def auth_headers() -> dict[str, str]:
    """Auth headers for test requests."""
    return {"X-API-Key": "aiplat_test123456"}


# =============================================================================
# Health & System
# =============================================================================


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_endpoint(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "ai-platform"
        assert data["version"] == "0.1.0"
        assert "dependencies" in data

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, client: AsyncClient) -> None:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")


# =============================================================================
# Chat API
# =============================================================================


class TestChatAPI:
    @pytest.mark.asyncio
    async def test_chat_requires_auth(self, client: AsyncClient) -> None:
        """Chat without auth should return 401."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "qwen-max",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_chat_validation_empty_messages(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Empty messages should return 422."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={"model": "qwen-max", "messages": []},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_validation_bad_temperature(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Temperature > 2.0 should return 422."""
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "qwen-max",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 5.0,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422


# =============================================================================
# Conversations API
# =============================================================================


class TestConversationsAPI:
    @pytest.mark.asyncio
    async def test_list_conversations(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/conversations/", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "items" in data["data"]
        assert "total" in data["data"]


# =============================================================================
# Knowledge Base API
# =============================================================================


class TestKnowledgeAPI:
    @pytest.mark.asyncio
    async def test_list_knowledge_bases(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/knowledge-bases/", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0

    @pytest.mark.asyncio
    async def test_create_knowledge_base(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.post(
            "/api/v1/knowledge-bases/",
            json={
                "name": "Test KB",
                "description": "Integration test knowledge base",
                "embedding_model": "text-embedding-3-small",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "Test KB"
        assert data["data"]["doc_count"] == 0

    @pytest.mark.asyncio
    async def test_get_nonexistent_kb(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        import uuid

        resp = await client.get(
            f"/api/v1/knowledge-bases/{uuid.uuid4()}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# =============================================================================
# Agents API
# =============================================================================


class TestAgentsAPI:
    @pytest.mark.asyncio
    async def test_list_agents(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get("/api/v1/agents/", headers=auth_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_agent(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.post(
            "/api/v1/agents/",
            json={
                "name": "Test Agent",
                "description": "Integration test agent",
                "model": "qwen-max",
                "tools": ["http_request"],
                "system_prompt": "You are a test assistant.",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "Test Agent"

    @pytest.mark.asyncio
    async def test_list_tools(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/agents/tools", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        tool_names = [t["name"] for t in data["data"]]
        assert "http_request" in tool_names
        assert "knowledge_search" in tool_names


# =============================================================================
# Models API
# =============================================================================


class TestModelsAPI:
    @pytest.mark.asyncio
    async def test_list_providers(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/models/providers", headers=auth_headers
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_models(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get("/api/v1/models/", headers=auth_headers)
        assert resp.status_code == 200


# =============================================================================
# Prompts API
# =============================================================================


class TestPromptsAPI:
    @pytest.mark.asyncio
    async def test_list_prompts(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get("/api/v1/prompts/", headers=auth_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_prompt(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.post(
            "/api/v1/prompts/",
            json={
                "name": "Test Prompt",
                "content": "You are a {{role}}. Answer: {{question}}",
                "description": "Test template",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "Test Prompt"
        assert data["data"]["current_version"] == 1


# =============================================================================
# Workflows API
# =============================================================================


class TestWorkflowsAPI:
    @pytest.mark.asyncio
    async def test_list_workflows(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get("/api/v1/workflows/", headers=auth_headers)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_workflow(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.post(
            "/api/v1/workflows/",
            json={
                "name": "Test Workflow",
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "llm_1", "type": "llm_call", "config": {"model": "qwen-max", "prompt": "{{inputs.question}}"}},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"source": "start", "target": "llm_1"},
                    {"source": "llm_1", "target": "end"},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "Test Workflow"
        assert data["data"]["node_count"] == 3

    @pytest.mark.asyncio
    async def test_create_workflow_invalid_dag(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Workflow without START node should fail validation."""
        resp = await client.post(
            "/api/v1/workflows/",
            json={
                "name": "Bad Workflow",
                "nodes": [
                    {"id": "llm_1", "type": "llm_call"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [
                    {"source": "llm_1", "target": "end"},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422


# =============================================================================
# Costs API
# =============================================================================


class TestCostsAPI:
    @pytest.mark.asyncio
    async def test_cost_summary(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/costs/summary", headers=auth_headers
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_daily_costs(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/costs/daily?days=7", headers=auth_headers
        )
        assert resp.status_code == 200


# =============================================================================
# Security Headers
# =============================================================================


class TestSecurityHeaders:
    @pytest.mark.asyncio
    async def test_security_headers_present(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
