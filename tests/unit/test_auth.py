"""Tests for authentication middleware — Redis degradation (Phase 0 — Task 3).

These tests do not exercise a real Redis connection. Instead they mock
``get_redis`` to either raise a ``RedisError`` (simulating an outage) or
return a fake client. This lets us prove that the auth path degrades
gracefully to the DB instead of 500-ing.

NOTE: We use ``asyncio.run(...)`` directly rather than ``pytest-asyncio``
because the test environment may not have the plugin installed.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from ai_platform.api.middleware import auth as auth_module
from ai_platform.api.middleware.auth import (
    _TTLCache,
    _check_tenant_status,
    _is_redis_error,
)


# ---------------------------------------------------------------------------
# _TTLCache basics
# ---------------------------------------------------------------------------


def test_ttl_cache_hit_and_miss() -> None:
    cache: _TTLCache = _TTLCache(maxsize=10, default_ttl_s=60.0)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    assert cache.get("missing") is None


def test_ttl_cache_delete() -> None:
    cache = _TTLCache()
    cache.set("k", "v")
    cache.delete("k")
    assert cache.get("k") is None


def test_ttl_cache_evicts_when_full() -> None:
    cache = _TTLCache(maxsize=2, default_ttl_s=60.0)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # should evict one of a/b
    assert cache.get("c") == 3
    # One of the first two must have been dropped.
    assert (cache.get("a") is None) or (cache.get("b") is None)


# ---------------------------------------------------------------------------
# _is_redis_error classification
# ---------------------------------------------------------------------------


def test_is_redis_error_true_for_redis_exception() -> None:
    try:
        import redis.exceptions as r_exc
    except ImportError:
        pytest.skip("redis package not installed")

    assert _is_redis_error(r_exc.RedisError("boom")) is True
    assert _is_redis_error(r_exc.ConnectionError("down")) is True
    assert _is_redis_error(r_exc.TimeoutError("slow")) is True


def test_is_redis_error_false_for_unrelated_exception() -> None:
    assert _is_redis_error(ValueError("nope")) is False
    assert _is_redis_error(RuntimeError("nope")) is False


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    def __init__(self, tenant_status: str | None) -> None:
        self._tenant_status = tenant_status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return _FakeResult(self._tenant_status)


class _FakeSessionFactory:
    def __init__(self, tenant_status: str | None) -> None:
        self._tenant_status = tenant_status

    def __call__(self):
        return _FakeSession(self._tenant_status)


class _RedisDown:
    """A fake Redis client that raises on every operation."""

    async def get(self, *_a, **_kw):
        import redis.exceptions as r_exc

        raise r_exc.ConnectionError("simulated outage")

    async def setex(self, *_a, **_kw):
        import redis.exceptions as r_exc

        raise r_exc.ConnectionError("simulated outage")


def _install_tenant_model_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``ai_platform.domain.models.Tenant`` resolve inside _check_tenant_status.

    The ``status`` and ``id`` attributes must look enough like SQLAlchemy
    column expressions for ``select(Tenant.status).where(Tenant.id == ...)``
    to build without raising.

    Uses monkeypatch so the real ``ai_platform.domain.models`` is restored
    after the test — without this, other tests that import models (Conversation,
    Agent, etc.) would see only our stub.
    """
    from sqlalchemy import Column, String
    from sqlalchemy.orm import DeclarativeBase

    class _Base(DeclarativeBase):
        pass

    class _TenantStub(_Base):
        __tablename__ = "tenants_stub"
        id = Column(String, primary_key=True)
        status = Column(String)

    # Get the real models module and make a copy with our Tenant injected
    import ai_platform.domain.models as real_models

    class _ModelsStub:
        Tenant = _TenantStub
        # Preserve other model imports so tests running in the same process
        # aren't affected if monkeypatch cleanup is delayed.
        Conversation = real_models.Conversation
        Agent = real_models.Agent
        Workflow = real_models.Workflow
        KnowledgeBase = real_models.KnowledgeBase
        PromptTemplate = real_models.PromptTemplate
        User = real_models.User
        Role = real_models.Role
        AuditLog = real_models.AuditLog

    monkeypatch.setitem(sys.modules, "ai_platform.domain.models", _ModelsStub)


# ---------------------------------------------------------------------------
# _check_tenant_status — Redis degradation to DB
# ---------------------------------------------------------------------------


def _clear_caches() -> None:
    auth_module._tenant_status_cache.clear()
    auth_module._perm_cache.clear()


def test_check_tenant_status_redis_down_falls_back_to_db_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Redis is down, tenant status must be read from DB without raising."""
    _clear_caches()
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000099")

    async def fake_get_redis():
        return _RedisDown()

    monkeypatch.setattr(auth_module, "get_redis", fake_get_redis)

    import ai_platform.infra.database.connection as db_conn

    factory = _FakeSessionFactory("active")
    monkeypatch.setattr(db_conn, "get_session_factory", lambda: factory)
    _install_tenant_model_stub(monkeypatch)

    result = asyncio.run(_check_tenant_status(tenant_id))
    assert result == "active"
    _clear_caches()


def test_check_tenant_status_redis_down_db_suspended_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_caches()
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000099")

    async def fake_get_redis():
        return _RedisDown()

    monkeypatch.setattr(auth_module, "get_redis", fake_get_redis)

    import ai_platform.infra.database.connection as db_conn

    factory = _FakeSessionFactory("suspended")
    monkeypatch.setattr(db_conn, "get_session_factory", lambda: factory)
    _install_tenant_model_stub(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_check_tenant_status(tenant_id))
    assert exc_info.value.status_code == 403
    _clear_caches()


def test_check_tenant_status_l1_cache_prevents_redis_and_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If L1 has the value, neither Redis nor DB is touched."""
    _clear_caches()
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000042")
    cache_key = f"aip:tenant_status:{tenant_id}"
    auth_module._tenant_status_cache.set(cache_key, "active", ttl_s=60.0)

    async def boom():
        raise AssertionError("Redis must not be called when L1 hits")

    monkeypatch.setattr(auth_module, "get_redis", boom)

    result = asyncio.run(_check_tenant_status(tenant_id))
    assert result == "active"
    _clear_caches()
