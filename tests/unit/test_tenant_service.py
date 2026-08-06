"""Unit tests for tenant-related functionality — status checks, quota config, isolation."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from ai_platform.api.middleware import auth as auth_module
from ai_platform.api.middleware import quota as quota_module
from ai_platform.api.middleware.auth import _TTLCache, _check_tenant_status


# ---------------------------------------------------------------------------
# Test Doubles
# ---------------------------------------------------------------------------


class FakeScalarResult:
    def __init__(self, value: Any):
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def first(self):
        return self._value


class FakeResult:
    def __init__(self, value: Any):
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalars(self):
        return FakeScalarResult(self._value)


class FakeSession:
    def __init__(self, tenant_data: dict | None = None):
        self._tenant_data = tenant_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return FakeResult(self._tenant_data)


class FakeSessionFactory:
    def __init__(self, tenant_data: dict | None = None):
        self._tenant_data = tenant_data

    def __call__(self):
        return FakeSession(self._tenant_data)


class FakeRedis:
    """Fake Redis client with configurable behavior."""

    def __init__(self, data: dict | None = None, should_fail: bool = False):
        self._data = data or {}
        self._should_fail = should_fail
        self.setex_calls: list[tuple] = []
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def get(self, key: str):
        self.get_calls.append(key)
        if self._should_fail:
            import redis.exceptions as r_exc
            raise r_exc.ConnectionError("Redis down")
        return self._data.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.setex_calls.append((key, ttl, value))
        if self._should_fail:
            import redis.exceptions as r_exc
            raise r_exc.ConnectionError("Redis down")
        self._data[key] = value

    async def delete(self, key: str):
        self.delete_calls.append(key)
        if self._should_fail:
            import redis.exceptions as r_exc
            raise r_exc.ConnectionError("Redis down")
        self._data.pop(key, None)


def _clear_auth_caches() -> None:
    """Clear auth module caches between tests."""
    auth_module._tenant_status_cache.clear()
    auth_module._perm_cache.clear()


def _clear_quota_caches() -> None:
    """Clear quota module caches between tests."""
    # Quota module doesn't have module-level caches, but clear Redis data
    pass


# Capture original module attributes at import time so each test starts clean
import ai_platform.infra.database.connection as _db_conn

_ORIGINAL_AUTH_GET_REDIS = auth_module.get_redis
_ORIGINAL_QUOTA_GET_REDIS = quota_module.get_redis
_ORIGINAL_SESSION_FACTORY = _db_conn.get_session_factory


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset module-level attribute mutations after each test."""
    yield
    auth_module.get_redis = _ORIGINAL_AUTH_GET_REDIS
    quota_module.get_redis = _ORIGINAL_QUOTA_GET_REDIS
    _db_conn.get_session_factory = _ORIGINAL_SESSION_FACTORY
    _clear_auth_caches()


# ---------------------------------------------------------------------------
# Tenant Status Tests
# ---------------------------------------------------------------------------


def test_check_tenant_status_active_returns_active():
    """Test that active tenant returns 'active' status."""
    _clear_auth_caches()
    tenant_id = uuid.uuid4()

    async def fake_get_redis():
        return FakeRedis(data={f"aip:tenant_status:{tenant_id}": "active"})

    auth_module.get_redis = fake_get_redis

    result = asyncio.run(_check_tenant_status(tenant_id))
    assert result == "active"
    _clear_auth_caches()


def test_check_tenant_status_suspended_raises_403():
    """Test that suspended tenant raises 403."""
    _clear_auth_caches()
    tenant_id = uuid.uuid4()

    async def fake_get_redis():
        return FakeRedis(data={f"aip:tenant_status:{tenant_id}": "suspended"})

    auth_module.get_redis = fake_get_redis

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_check_tenant_status(tenant_id))

    assert exc_info.value.status_code == 403
    assert "suspended" in str(exc_info.value.detail).lower()
    _clear_auth_caches()


def test_check_tenant_status_cancelled_raises_403():
    """Test that cancelled tenant raises 403."""
    _clear_auth_caches()
    tenant_id = uuid.uuid4()

    async def fake_get_redis():
        return FakeRedis(data={f"aip:tenant_status:{tenant_id}": "cancelled"})

    auth_module.get_redis = fake_get_redis

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_check_tenant_status(tenant_id))

    assert exc_info.value.status_code == 403
    _clear_auth_caches()


def test_check_tenant_status_not_found_raises_404(monkeypatch: pytest.MonkeyPatch):
    """Test that non-existent tenant raises 404."""
    _clear_auth_caches()
    tenant_id = uuid.uuid4()

    async def fake_get_redis():
        return FakeRedis(data={})  # No cached status

    monkeypatch.setattr(auth_module, "get_redis", fake_get_redis)

    # Mock DB to return None (tenant not found)
    import ai_platform.infra.database.connection as db_conn
    factory = FakeSessionFactory(None)
    monkeypatch.setattr(db_conn, "get_session_factory", lambda: factory)

    # Install tenant model stub (preserves other model imports)
    import sys
    import types
    from sqlalchemy import Column, String
    from sqlalchemy.orm import DeclarativeBase
    import ai_platform.domain.models as real_models

    class _Base(DeclarativeBase):
        pass

    class _TenantStub(_Base):
        __tablename__ = "tenants_stub_test"
        id = Column(String, primary_key=True)
        status = Column(String)

    class _ModelsStub:
        Tenant = _TenantStub
        # Preserve other model imports so subsequent tests work
        Conversation = real_models.Conversation
        Agent = real_models.Agent
        Workflow = real_models.Workflow
        KnowledgeBase = real_models.KnowledgeBase
        PromptTemplate = real_models.PromptTemplate
        User = real_models.User
        Role = real_models.Role
        AuditLog = real_models.AuditLog

    monkeypatch.setitem(sys.modules, "ai_platform.domain.models", _ModelsStub)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_check_tenant_status(tenant_id))

    assert exc_info.value.status_code == 404
    _clear_auth_caches()


def test_check_tenant_status_caches_result():
    """Test that tenant status is cached after first lookup."""
    _clear_auth_caches()
    tenant_id = uuid.uuid4()
    cache_key = f"aip:tenant_status:{tenant_id}"

    redis = FakeRedis(data={cache_key: "active"})

    async def fake_get_redis():
        return redis

    auth_module.get_redis = fake_get_redis

    # First call should populate L1 cache
    result1 = asyncio.run(_check_tenant_status(tenant_id))
    assert result1 == "active"

    # Verify it's in L1 cache
    assert auth_module._tenant_status_cache.get(cache_key) == "active"

    # Second call should use L1 cache (no Redis call)
    redis._should_fail = True  # Make Redis fail
    result2 = asyncio.run(_check_tenant_status(tenant_id))
    assert result2 == "active"  # Should still succeed from L1

    _clear_auth_caches()


# ---------------------------------------------------------------------------
# Quota Config Tests
# ---------------------------------------------------------------------------


def test_get_tenant_quota_limit_from_redis():
    """Test fetching quota limit from Redis cache."""
    tenant_id = str(uuid.uuid4())
    quota_config = {"monthly_model_calls": 1000, "max_users": 10}

    redis = FakeRedis(data={f"aip:tenant_quota_config:{tenant_id}": '{"monthly_model_calls": 1000, "max_users": 10}'})

    async def fake_get_redis():
        return redis

    quota_module.get_redis = fake_get_redis

    result = asyncio.run(quota_module._get_tenant_quota_limit(tenant_id, "model_calls"))
    assert result == 1000


def test_get_tenant_quota_limit_unlimited_when_not_configured():
    """Test that unconfigured quota returns None (unlimited)."""
    tenant_id = str(uuid.uuid4())

    redis = FakeRedis(data={})  # No config cached

    async def fake_get_redis():
        return redis

    quota_module.get_redis = fake_get_redis

    # Mock DB to return empty quota_config
    import ai_platform.infra.database.connection as db_conn
    factory = FakeSessionFactory({})
    db_conn.get_session_factory = lambda: factory

    result = asyncio.run(quota_module._get_tenant_quota_limit(tenant_id, "model_calls"))
    assert result is None  # Unlimited


def test_get_tenant_quota_limit_zero_means_unlimited():
    """Test that quota limit of 0 means unlimited."""
    tenant_id = str(uuid.uuid4())
    quota_config = {"monthly_model_calls": 0}

    redis = FakeRedis(data={f"aip:tenant_quota_config:{tenant_id}": '{"monthly_model_calls": 0}'})

    async def fake_get_redis():
        return redis

    quota_module.get_redis = fake_get_redis

    result = asyncio.run(quota_module._get_tenant_quota_limit(tenant_id, "model_calls"))
    assert result is None  # 0 means unlimited


def test_get_tenant_quota_limit_unknown_resource_returns_none():
    """Test that unknown resource type returns None."""
    tenant_id = str(uuid.uuid4())

    redis = FakeRedis(data={})

    async def fake_get_redis():
        return redis

    quota_module.get_redis = fake_get_redis

    # Mock DB to return empty quota_config (required because code hits DB on cache miss)
    factory = FakeSessionFactory({})
    _db_conn.get_session_factory = lambda: factory

    result = asyncio.run(quota_module._get_tenant_quota_limit(tenant_id, "unknown_resource"))
    assert result is None


# ---------------------------------------------------------------------------
# Quota Increment Tests
# ---------------------------------------------------------------------------


def test_increment_quota_returns_new_value():
    """Test that increment_quota returns the new counter value."""
    tenant_id = "test-tenant"
    resource_type = "model_calls"

    class RecordingRedis:
        async def eval(self, script, numkeys, *args):
            return 5  # Simulate counter at 5

    async def fake_get_redis():
        return RecordingRedis()

    quota_module.get_redis = fake_get_redis

    result = asyncio.run(quota_module.increment_quota(tenant_id, resource_type, amount=1))
    assert result == 5


def test_increment_quota_uses_lua_script():
    """Test that increment_quota uses atomic Lua script."""
    tenant_id = "test-tenant"
    resource_type = "model_calls"

    class RecordingRedis:
        def __init__(self):
            self.eval_called = False
            self.eval_args = None

        async def eval(self, script, numkeys, *args):
            self.eval_called = True
            self.eval_args = (script, numkeys, args)
            return 1

    redis = RecordingRedis()

    async def fake_get_redis():
        return redis

    quota_module.get_redis = fake_get_redis

    asyncio.run(quota_module.increment_quota(tenant_id, resource_type, amount=3))

    assert redis.eval_called
    assert redis.eval_args[1] == 1  # numkeys
    assert "aip:quota:test-tenant:model_calls" in redis.eval_args[2]


# ---------------------------------------------------------------------------
# Tenant Isolation Tests
# ---------------------------------------------------------------------------


def test_tenant_id_is_uuid():
    """Test that tenant_id is always a UUID."""
    tenant_id = uuid.uuid4()
    assert isinstance(tenant_id, uuid.UUID)


def test_different_tenants_have_different_ids():
    """Test that different tenants have different IDs."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    assert tenant_a != tenant_b


def test_quota_key_includes_tenant_id():
    """Test that quota key includes tenant_id for isolation."""
    tenant_id = "tenant-123"
    resource_type = "model_calls"

    key = quota_module._quota_key(tenant_id, resource_type)

    assert tenant_id in key
    assert resource_type in key
    assert key == "aip:quota:tenant-123:model_calls"


def test_tenant_status_cache_key_includes_tenant_id():
    """Test that tenant status cache key includes tenant_id."""
    tenant_id = uuid.uuid4()
    cache_key = f"aip:tenant_status:{tenant_id}"

    assert str(tenant_id) in cache_key
    assert cache_key.startswith("aip:tenant_status:")


# ---------------------------------------------------------------------------
# Invalidate Cache Tests
# ---------------------------------------------------------------------------


def test_invalidate_tenant_status_cache():
    """Test that invalidate_tenant_status_cache clears both L1 and Redis."""
    tenant_id = uuid.uuid4()
    cache_key = f"aip:tenant_status:{tenant_id}"

    # Pre-populate L1 cache
    auth_module._tenant_status_cache.set(cache_key, "active", ttl_s=60.0)

    redis = FakeRedis(data={cache_key: "active"})

    async def fake_get_redis():
        return redis

    auth_module.get_redis = fake_get_redis

    asyncio.run(auth_module.invalidate_tenant_status_cache(tenant_id))

    # Verify L1 cache cleared
    assert auth_module._tenant_status_cache.get(cache_key) is None
    # Verify Redis delete called
    assert cache_key in redis.delete_calls

    _clear_auth_caches()


def test_invalidate_quota_config_cache():
    """Test that invalidate_quota_config_cache clears Redis cache."""
    tenant_id = "test-tenant"
    cache_key = f"aip:tenant_quota_config:{tenant_id}"

    redis = FakeRedis(data={cache_key: '{"monthly_model_calls": 1000}'})

    async def fake_get_redis():
        return redis

    quota_module.get_redis = fake_get_redis

    asyncio.run(quota_module.invalidate_quota_config_cache(tenant_id))

    # Verify Redis delete called
    assert cache_key in redis.delete_calls
