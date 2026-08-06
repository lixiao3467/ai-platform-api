"""Integration tests — Models/Providers API end-to-end with mocked DB."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-for-testing-only")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_platform_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeModelProvider:
    """Lightweight stand-in for ``domain.models.ModelProvider``."""

    def __init__(
        self,
        *,
        id: uuid.UUID | None = None,
        tenant_id: uuid.UUID,
        provider_name: str,
        display_name: str | None = None,
        api_base_url: str | None = None,
        api_key_ref: str | None = None,
        models: list[dict[str, Any]] | None = None,
        is_enabled: bool = True,
        priority: int = 0,
        needs_retest: bool = False,
        last_test_at: datetime | None = None,
        last_test_success: bool | None = None,
        last_test_latency_ms: int | None = None,
    ) -> None:
        self.id = id or uuid.uuid4()
        self.tenant_id = tenant_id
        self.provider_name = provider_name
        self.display_name = display_name or provider_name
        self.api_base_url = api_base_url
        self.api_key_ref = api_key_ref
        self.models = models or []
        self.is_enabled = is_enabled
        self.priority = priority
        self.needs_retest = needs_retest
        self.last_test_at = last_test_at
        self.last_test_success = last_test_success
        self.last_test_latency_ms = last_test_latency_ms
        self.created_at = datetime.now(timezone.utc)


class FakeScalars:
    def __init__(self, values: list) -> None:
        self._values = values

    def all(self) -> list:
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class FakeResult:
    def __init__(self, values: list) -> None:
        self._values = values

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._values)


class FakeSession:
    """In-memory SQLAlchemy-like session for integration tests."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, FakeModelProvider] = {}

    async def get(self, model_class, provider_id: uuid.UUID):
        return self._store.get(provider_id)

    def add(self, instance: FakeModelProvider) -> None:
        self._store[instance.id] = instance

    async def flush(self) -> None:
        pass

    async def execute(self, stmt):
        """Return providers matching simple criteria."""
        return FakeResult(list(self._store.values()))

    async def delete(self, instance: FakeModelProvider) -> None:
        self._store.pop(instance.id, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def mock_request_context():
    """Mock RequestContext with test tenant."""
    from ai_platform.api.middleware.auth import RequestContext

    ctx = MagicMock(spec=RequestContext)
    ctx.tenant_id = TENANT_ID
    ctx.user_id = "test-user-id"
    ctx.app_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    ctx.role = "admin"
    ctx.permissions = ["model.read", "model.manage"]
    return ctx


@pytest.fixture
async def client(fake_session: FakeSession, mock_request_context):
    """Create an async test client with mocked dependencies."""
    from ai_platform.main import app
    from ai_platform.infra.database.connection import get_db
    from ai_platform.api.middleware.auth import get_request_context

    # Override dependencies
    async def override_get_db():
        yield fake_session

    async def override_get_request_context():
        return mock_request_context

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_request_context] = override_get_request_context

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    # Clean up overrides
    app.dependency_overrides.clear()


# =============================================================================
# T-02: Integration tests for Models API
# =============================================================================


class TestModelsAPI:
    """T-02: Integration tests for /api/v1/models/* endpoints."""

    @pytest.mark.asyncio
    async def test_create_provider_returns_fields(self, client: AsyncClient) -> None:
        """T-02.1: POST /providers response includes needs_retest/last_test_at fields."""
        resp = await client.post(
            "/api/v1/models/providers",
            json={
                "provider_name": "openai",
                "display_name": "OpenAI",
                "api_key": "sk-test-key",
                "models": [{"name": "gpt-4o", "enabled": True}],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        provider_data = data["data"]
        assert "needs_retest" in provider_data
        assert "last_test_at" in provider_data
        assert "last_test_success" in provider_data
        assert "last_test_latency_ms" in provider_data
        assert provider_data["provider_name"] == "openai"

    @pytest.mark.asyncio
    async def test_list_providers_includes_test_fields(self, client: AsyncClient) -> None:
        """T-02.2: GET /providers response includes test status fields."""
        # Create a provider first
        await client.post(
            "/api/v1/models/providers",
            json={
                "provider_name": "anthropic",
                "display_name": "Anthropic",
                "api_key": "sk-test-key",
            },
        )

        resp = await client.get("/api/v1/models/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        providers = data["data"]
        assert isinstance(providers, list)
        assert len(providers) > 0

        provider = providers[0]
        assert "needs_retest" in provider
        assert "last_test_at" in provider
        assert "last_test_success" in provider
        assert "last_test_latency_ms" in provider

    @pytest.mark.asyncio
    async def test_update_provider_sets_needs_retest(self, client: AsyncClient) -> None:
        """T-02.3: PUT /providers/{id} with api_key sets needs_retest=true."""
        # Create
        create_resp = await client.post(
            "/api/v1/models/providers",
            json={
                "provider_name": "openai",
                "api_key": "sk-old-key",
            },
        )
        provider_id = create_resp.json()["data"]["id"]

        # Update with new api_key
        update_resp = await client.put(
            f"/api/v1/models/providers/{provider_id}",
            json={"api_key": "sk-new-key"},
        )
        assert update_resp.status_code == 200
        data = update_resp.json()["data"]
        assert data["needs_retest"] is True
        assert data["is_enabled"] is False

    @pytest.mark.asyncio
    async def test_toggle_provider_400_when_needs_retest(self, client: AsyncClient) -> None:
        """T-02.4: PUT toggle?enabled=true returns 400 when needs_retest=True."""
        # Create and update to trigger needs_retest
        create_resp = await client.post(
            "/api/v1/models/providers",
            json={
                "provider_name": "openai",
                "api_key": "sk-test-key",
            },
        )
        provider_id = create_resp.json()["data"]["id"]

        # Update api_key to trigger needs_retest
        await client.put(
            f"/api/v1/models/providers/{provider_id}",
            json={"api_key": "sk-new-key"},
        )

        # Try to enable
        toggle_resp = await client.put(
            f"/api/v1/models/providers/{provider_id}/toggle?enabled=true"
        )
        assert toggle_resp.status_code == 400
        assert "needs connectivity test" in toggle_resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_toggle_provider_200_after_test(self, client: AsyncClient) -> None:
        """T-02.5: POST test then PUT toggle?enabled=true returns 200."""
        # Create
        create_resp = await client.post(
            "/api/v1/models/providers",
            json={
                "provider_name": "openai",
                "api_key": "sk-test-key",
                "models": [{"name": "gpt-4o", "enabled": True}],
            },
        )
        provider_id = create_resp.json()["data"]["id"]

        # Update to trigger needs_retest
        await client.put(
            f"/api/v1/models/providers/{provider_id}",
            json={"api_key": "sk-new-key"},
        )

        # Mock litellm and run test
        with patch("ai_platform.core.model_router.litellm_client._get_litellm") as mock_get_litellm:
            mock_litellm = MagicMock()
            mock_litellm.acompletion = AsyncMock(return_value=MagicMock())
            mock_get_litellm.return_value = mock_litellm

            test_resp = await client.post(
                f"/api/v1/models/providers/{provider_id}/test"
            )
            assert test_resp.status_code == 200
            assert test_resp.json()["data"]["success"] is True

        # Now toggle should succeed
        toggle_resp = await client.put(
            f"/api/v1/models/providers/{provider_id}/toggle?enabled=true"
        )
        assert toggle_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_toggle_model_enabled_validation(self, client: AsyncClient) -> None:
        """T-02.6: POST toggle model without 'enabled' field returns 422."""
        # Create provider
        create_resp = await client.post(
            "/api/v1/models/providers",
            json={
                "provider_name": "openai",
                "api_key": "sk-test-key",
                "models": [{"name": "gpt-4o", "enabled": True}],
            },
        )
        provider_id = create_resp.json()["data"]["id"]

        # Try to toggle without enabled field
        resp = await client.post(
            f"/api/v1/models/providers/{provider_id}/models/gpt-4o/toggle",
            json={},  # Missing 'enabled' field
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_api_key_disables_provider(self, client: AsyncClient) -> None:
        """T-02.7: PUT /providers/{id}/key sets is_enabled=false."""
        # Create
        create_resp = await client.post(
            "/api/v1/models/providers",
            json={
                "provider_name": "openai",
                "api_key": "sk-old-key",
            },
        )
        provider_id = create_resp.json()["data"]["id"]

        # Update key
        update_resp = await client.put(
            f"/api/v1/models/providers/{provider_id}/key",
            json={"api_key": "sk-new-key"},
        )
        assert update_resp.status_code == 200

        # Verify provider is disabled
        get_resp = await client.get("/api/v1/models/providers")
        providers = get_resp.json()["data"]
        provider = next(p for p in providers if p["id"] == provider_id)
        assert provider["is_enabled"] is False
        assert provider["needs_retest"] is True
