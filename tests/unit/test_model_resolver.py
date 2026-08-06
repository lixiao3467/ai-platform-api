"""Unit tests for ModelResolverService — purpose filtering, priority, fallback."""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any

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
        tenant_id: uuid.UUID | None,
        provider_name: str,
        display_name: str | None = None,
        api_base_url: str | None = None,
        api_key_ref: str | None = None,
        models: list[dict[str, Any]],
        is_enabled: bool = True,
        priority: int = 0,
    ) -> None:
        self.id = id
        self.tenant_id = tenant_id
        self.provider_name = provider_name
        self.display_name = display_name or provider_name
        self.api_base_url = api_base_url
        self.api_key_ref = api_key_ref
        self.models = models
        self.is_enabled = is_enabled
        self.priority = priority


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
    """Routes queries to two pre-built provider lists (tenant vs global).

    ``_get_all_enabled_providers`` issues two SELECTs in sequence:
    the first returns tenant providers, the second returns global providers.
    We alternate between the two lists on each ``execute`` call.
    """

    def __init__(
        self,
        tenant_providers: list[FakeModelProvider] | None = None,
        global_providers: list[FakeModelProvider] | None = None,
        single: Any | None = None,
    ) -> None:
        self._tenant = tenant_providers or []
        self._global = global_providers or []
        self._single = single
        self._call_idx = 0  # even → tenant, odd → global

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        result = self._tenant if self._call_idx % 2 == 0 else self._global
        self._call_idx += 1
        return FakeResult(result)

    async def get(self, _model, provider_id):
        return self._single

    async def flush(self):
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_available_filters_by_purpose():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    providers = [
        FakeModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name="openai",
            models=[
                {
                    "name": "gpt-4o",
                    "purposes": ["llm", "vision"],
                    "enabled": True,
                    "context_length": 128000,
                },
                {
                    "name": "text-embedding-3-small",
                    "purposes": ["embedding"],
                    "enabled": True,
                },
            ],
            priority=10,
        ),
    ]

    session = FakeSession(tenant_providers=providers)
    resolver = ModelResolverService(session)

    # Filter by "llm"
    llm_items = await resolver.list_available(tenant_id, purpose="llm")
    assert len(llm_items) == 1
    assert llm_items[0].model_name == "gpt-4o"
    assert "llm" in llm_items[0].purposes

    # Filter by "embedding"
    emb_items = await resolver.list_available(tenant_id, purpose="embedding")
    assert len(emb_items) == 1
    assert emb_items[0].model_name == "text-embedding-3-small"

    # No filter → returns all enabled models
    all_items = await resolver.list_available(tenant_id, purpose=None)
    assert len(all_items) == 2


@pytest.mark.asyncio
async def test_list_available_excludes_disabled_models():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    providers = [
        FakeModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name="openai",
            models=[
                {"name": "gpt-4o", "purposes": ["llm"], "enabled": True},
                {"name": "gpt-3.5", "purposes": ["llm"], "enabled": False},
                # legacy row: missing 'enabled' → treated as True
                {"name": "gpt-legacy", "purposes": ["llm"]},
            ],
            priority=5,
        ),
    ]

    session = FakeSession(tenant_providers=providers)
    resolver = ModelResolverService(session)

    items = await resolver.list_available(tenant_id, purpose="llm")
    names = {i.model_name for i in items}
    assert names == {"gpt-4o", "gpt-legacy"}


@pytest.mark.asyncio
async def test_list_available_sorts_by_priority_desc():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    providers = [
        FakeModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name="low",
            models=[{"name": "a", "purposes": ["llm"], "enabled": True}],
            priority=1,
        ),
        FakeModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name="high",
            models=[{"name": "b", "purposes": ["llm"], "enabled": True}],
            priority=100,
        ),
    ]

    session = FakeSession(tenant_providers=providers)
    resolver = ModelResolverService(session)

    items = await resolver.list_available(tenant_id, purpose="llm")
    assert items[0].model_name == "b"
    assert items[0].priority == 100
    assert items[1].model_name == "a"


@pytest.mark.asyncio
async def test_get_default_for_purpose_returns_highest_priority():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    providers = [
        FakeModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name="fallback",
            models=[{"name": "slow-model", "purposes": ["llm"], "enabled": True}],
            priority=1,
        ),
        FakeModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name="preferred",
            models=[{"name": "fast-model", "purposes": ["llm"], "enabled": True}],
            priority=50,
        ),
    ]

    session = FakeSession(tenant_providers=providers)
    resolver = ModelResolverService(session)

    config = await resolver.get_default_for_purpose(tenant_id, "llm")
    assert config is not None
    assert config.model_name == "fast-model"
    assert config.provider_name == "preferred"
    assert config.priority == 50


@pytest.mark.asyncio
async def test_get_default_for_purpose_returns_none_when_no_match():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    providers = [
        FakeModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name="openai",
            models=[{"name": "gpt-4o", "purposes": ["llm"], "enabled": True}],
            priority=10,
        ),
    ]

    session = FakeSession(tenant_providers=providers)
    resolver = ModelResolverService(session)

    # No embedding model configured
    config = await resolver.get_default_for_purpose(tenant_id, "embedding")
    assert config is None


@pytest.mark.asyncio
async def test_get_default_for_purpose_skips_disabled_models():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    providers = [
        FakeModelProvider(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            provider_name="openai",
            models=[
                {"name": "high-but-disabled", "purposes": ["llm"], "enabled": False},
                {"name": "lower-enabled", "purposes": ["llm"], "enabled": True},
            ],
            priority=10,
        ),
    ]

    session = FakeSession(tenant_providers=providers)
    resolver = ModelResolverService(session)

    config = await resolver.get_default_for_purpose(tenant_id, "llm")
    assert config is not None
    assert config.model_name == "lower-enabled"


def test_env_fallback_returns_config_for_known_purposes():
    from ai_platform.services.model_resolver import ModelResolverService

    llm = ModelResolverService.get_env_fallback("llm")
    assert llm is not None
    assert llm.provider_name == "env"
    assert llm.model_name  # non-empty

    emb = ModelResolverService.get_env_fallback("embedding")
    assert emb is not None
    assert emb.purposes == ["embedding"]

    # Unknown purpose → None
    assert ModelResolverService.get_env_fallback("vision") is None


@pytest.mark.asyncio
async def test_toggle_model_flips_enabled_state():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    provider = FakeModelProvider(
        id=provider_id,
        tenant_id=tenant_id,
        provider_name="openai",
        models=[
            {"name": "gpt-4o", "purposes": ["llm"], "enabled": True},
        ],
        priority=10,
    )

    session = FakeSession(single=provider)
    resolver = ModelResolverService(session)

    new_state = await resolver.toggle_model(tenant_id, provider_id, "gpt-4o")
    assert new_state is False
    assert provider.models[0]["enabled"] is False

    new_state = await resolver.toggle_model(tenant_id, provider_id, "gpt-4o")
    assert new_state is True
    assert provider.models[0]["enabled"] is True


@pytest.mark.asyncio
async def test_toggle_model_raises_for_unknown_model():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    provider = FakeModelProvider(
        id=provider_id,
        tenant_id=tenant_id,
        provider_name="openai",
        models=[{"name": "gpt-4o", "purposes": ["llm"], "enabled": True}],
        priority=10,
    )

    session = FakeSession(single=provider)
    resolver = ModelResolverService(session)

    with pytest.raises(ValueError, match="not found"):
        await resolver.toggle_model(tenant_id, provider_id, "does-not-exist")


@pytest.mark.asyncio
async def test_toggle_model_enforces_tenant_isolation():
    from ai_platform.services.model_resolver import ModelResolverService

    owner_tenant = uuid.uuid4()
    other_tenant = uuid.uuid4()
    provider_id = uuid.uuid4()
    provider = FakeModelProvider(
        id=provider_id,
        tenant_id=owner_tenant,
        provider_name="openai",
        models=[{"name": "gpt-4o", "purposes": ["llm"], "enabled": True}],
        priority=10,
    )

    session = FakeSession(single=provider)
    resolver = ModelResolverService(session)

    with pytest.raises(ValueError, match="not found"):
        await resolver.toggle_model(other_tenant, provider_id, "gpt-4o")


@pytest.mark.asyncio
async def test_global_providers_used_as_fallback():
    from ai_platform.services.model_resolver import ModelResolverService

    tenant_id = uuid.uuid4()
    global_provider = FakeModelProvider(
        id=uuid.uuid4(),
        tenant_id=None,  # global
        provider_name="shared",
        models=[{"name": "shared-llm", "purposes": ["llm"], "enabled": True}],
        priority=5,
    )

    session = FakeSession(
        tenant_providers=[],
        global_providers=[global_provider],
    )
    resolver = ModelResolverService(session)

    items = await resolver.list_available(tenant_id, purpose="llm")
    assert len(items) == 1
    assert items[0].model_name == "shared-llm"

    config = await resolver.get_default_for_purpose(tenant_id, "llm")
    assert config is not None
    assert config.model_name == "shared-llm"
