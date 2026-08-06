"""Unit tests for ProviderService — encryption, auto-disable, connectivity test."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        id: uuid.UUID,
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
        self.id = id
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


class FakeSession:
    """In-memory SQLAlchemy-like session for unit tests."""

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, FakeModelProvider] = {}
        self._added: list[FakeModelProvider] = []

    async def get(self, model_class, provider_id: uuid.UUID):
        return self._store.get(provider_id)

    def add(self, instance: FakeModelProvider) -> None:
        self._added.append(instance)
        self._store[instance.id] = instance

    async def flush(self) -> None:
        pass

    async def execute(self, stmt):
        """Return providers matching simple criteria."""
        # For simplicity, return all providers
        return FakeResult(list(self._store.values()))

    async def delete(self, instance: FakeModelProvider) -> None:
        self._store.pop(instance.id, None)


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProviderService:
    """T-01: Unit tests for ProviderService."""

    @pytest.fixture
    def tenant_id(self) -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000001")

    @pytest.fixture
    def session(self) -> FakeSession:
        return FakeSession()

    @pytest.mark.asyncio
    async def test_create_provider(self, tenant_id: uuid.UUID, session: FakeSession) -> None:
        """T-01.1: Create provider, verify encrypted storage."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        api_key = "sk-test-key-12345"

        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            display_name="OpenAI",
            api_key=api_key,
            models=[{"name": "gpt-4o", "enabled": True}],
        )

        assert provider.id is not None
        assert provider.tenant_id == tenant_id
        assert provider.provider_name == "openai"
        assert provider.display_name == "OpenAI"
        assert provider.api_key_ref is not None
        # Key must be encrypted (not plaintext)
        assert provider.api_key_ref != api_key
        assert provider.is_enabled is True

    @pytest.mark.asyncio
    async def test_update_provider_auto_disable(self, tenant_id: uuid.UUID, session: FakeSession) -> None:
        """T-01.2: Updating api_key/api_base_url sets is_enabled=False + needs_retest=True."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            api_key="sk-old-key",
        )
        provider.is_enabled = True
        provider.needs_retest = False

        # Update api_key
        updated_provider, needs_retest = await svc.update_provider(
            provider.id,
            api_key="sk-new-key",
        )

        assert needs_retest is True
        assert updated_provider.is_enabled is False
        assert updated_provider.needs_retest is True

    @pytest.mark.asyncio
    async def test_update_provider_no_reset_on_display_name(
        self, tenant_id: uuid.UUID, session: FakeSession
    ) -> None:
        """T-01.3: Changing only display_name does NOT trigger auto-disable."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            api_key="sk-test-key",
        )
        provider.is_enabled = True
        provider.needs_retest = False

        updated_provider, needs_retest = await svc.update_provider(
            provider.id,
            display_name="New Display Name",
        )

        assert needs_retest is False
        assert updated_provider.is_enabled is True
        assert updated_provider.needs_retest is False
        assert updated_provider.display_name == "New Display Name"

    @pytest.mark.asyncio
    async def test_test_provider_persists_result(
        self, tenant_id: uuid.UUID, session: FakeSession
    ) -> None:
        """T-01.4: test_provider() updates last_test_at/success/latency_ms in DB."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            api_key="sk-test-key",
            models=[{"name": "gpt-4o", "enabled": True}],
        )

        # Mock litellm.acompletion
        with patch("ai_platform.core.model_router.litellm_client._get_litellm") as mock_get_litellm:
            mock_litellm = MagicMock()
            mock_litellm.acompletion = AsyncMock(return_value=MagicMock())
            mock_get_litellm.return_value = mock_litellm

            result = await svc.test_provider(provider.id)

        assert result["success"] is True
        assert result["latency_ms"] >= 0
        assert result["model"] == "gpt-4o"

        # Verify DB was updated
        db_provider = await session.get(None, provider.id)
        assert db_provider.last_test_at is not None
        assert db_provider.last_test_success is True
        assert db_provider.last_test_latency_ms is not None

    @pytest.mark.asyncio
    async def test_test_provider_success_clears_needs_retest(
        self, tenant_id: uuid.UUID, session: FakeSession
    ) -> None:
        """T-01.5: Successful test sets needs_retest=False."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            api_key="sk-test-key",
            models=[{"name": "gpt-4o", "enabled": True}],
        )
        provider.needs_retest = True

        with patch("ai_platform.core.model_router.litellm_client._get_litellm") as mock_get_litellm:
            mock_litellm = MagicMock()
            mock_litellm.acompletion = AsyncMock(return_value=MagicMock())
            mock_get_litellm.return_value = mock_litellm

            await svc.test_provider(provider.id)

        db_provider = await session.get(None, provider.id)
        assert db_provider.needs_retest is False

    @pytest.mark.asyncio
    async def test_toggle_provider_blocked_when_needs_retest(
        self, tenant_id: uuid.UUID, session: FakeSession
    ) -> None:
        """T-01.6: toggle(enabled=True) raises ValueError when needs_retest=True."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            api_key="sk-test-key",
        )
        provider.needs_retest = True
        provider.is_enabled = False

        with pytest.raises(ValueError, match="needs connectivity test"):
            await svc.toggle_provider(provider.id, enabled=True)

    @pytest.mark.asyncio
    async def test_toggle_provider_allowed_after_test(
        self, tenant_id: uuid.UUID, session: FakeSession
    ) -> None:
        """T-01.7: toggle(enabled=True) succeeds after successful test."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            api_key="sk-test-key",
            models=[{"name": "gpt-4o", "enabled": True}],
        )
        provider.needs_retest = True
        provider.is_enabled = False

        # Run successful test
        with patch("ai_platform.core.model_router.litellm_client._get_litellm") as mock_get_litellm:
            mock_litellm = MagicMock()
            mock_litellm.acompletion = AsyncMock(return_value=MagicMock())
            mock_get_litellm.return_value = mock_litellm

            await svc.test_provider(provider.id)

        # Now toggle should succeed
        await svc.toggle_provider(provider.id, enabled=True)
        db_provider = await session.get(None, provider.id)
        assert db_provider.is_enabled is True

    @pytest.mark.asyncio
    async def test_get_key_for_model_skips_disabled_model(
        self, tenant_id: uuid.UUID, session: FakeSession
    ) -> None:
        """T-01.8: Disabled model in config is not returned."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            api_key="sk-test-key",
            api_base_url="https://api.openai.com/v1",
            models=[
                {"name": "gpt-4o", "enabled": False},  # Disabled
            ],
        )
        provider.is_enabled = True

        api_key, api_base_url = await svc.get_key_for_model(tenant_id, "gpt-4o")

        # Should return None because model is disabled
        assert api_key is None
        assert api_base_url is None

    @pytest.mark.asyncio
    async def test_update_api_key_triggers_retest(
        self, tenant_id: uuid.UUID, session: FakeSession
    ) -> None:
        """T-01.9: update_api_key() sets needs_retest=True + is_enabled=False."""
        from ai_platform.services.provider_service import ProviderService

        svc = ProviderService(session)
        provider = await svc.create_provider(
            tenant_id,
            provider_name="openai",
            api_key="sk-old-key",
        )
        provider.is_enabled = True
        provider.needs_retest = False

        await svc.update_api_key(provider.id, "sk-new-key")

        db_provider = await session.get(None, provider.id)
        assert db_provider.needs_retest is True
        assert db_provider.is_enabled is False
        assert db_provider.api_key_ref is not None
        # Verify key was re-encrypted
        assert db_provider.api_key_ref != "sk-new-key"
