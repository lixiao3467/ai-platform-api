"""Tests for quota middleware — atomic Lua increment (Phase 0 — Task 4).

NOTE: We use ``asyncio.run(...)`` directly to avoid depending on pytest-asyncio.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_platform.api.middleware import quota as quota_module
from ai_platform.api.middleware.quota import (
    _QUOTA_INCREMENT_LUA,
    _QUOTA_TTL_SECONDS,
    increment_quota,
)


class _RecordingRedis:
    """Fake Redis that records ``eval`` calls and returns scripted values."""

    def __init__(self, sequence: list[int] | None = None) -> None:
        self._sequence = list(sequence or [])
        self.eval_calls: list[dict[str, Any]] = []

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        self.eval_calls.append(
            {"script": script, "numkeys": numkeys, "args": tuple(args)}
        )
        if self._sequence:
            return self._sequence.pop(0)
        return 0


def test_increment_quota_calls_lua_with_correct_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingRedis(sequence=[5])

    async def fake_get_redis():
        return fake

    monkeypatch.setattr(quota_module, "get_redis", fake_get_redis)

    result = asyncio.run(increment_quota("tenant-1", "model_calls", amount=3))
    assert result == 5

    assert len(fake.eval_calls) == 1
    call = fake.eval_calls[0]
    assert call["script"] == _QUOTA_INCREMENT_LUA
    assert call["numkeys"] == 1
    assert call["args"] == ("aip:quota:tenant-1:model_calls", 3, _QUOTA_TTL_SECONDS)


def test_increment_quota_default_amount_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingRedis(sequence=[1])

    async def fake_get_redis():
        return fake

    monkeypatch.setattr(quota_module, "get_redis", fake_get_redis)

    asyncio.run(increment_quota("tenant-1", "model_calls"))
    call = fake.eval_calls[0]
    assert call["args"][1] == 1  # amount


def test_lua_script_sets_expire_only_on_first_increment() -> None:
    """The Lua script must call EXPIRE only when the key is brand new."""
    # Parse the Lua script text and assert the conditional is present.
    assert "INCRBY" in _QUOTA_INCREMENT_LUA
    assert "EXPIRE" in _QUOTA_INCREMENT_LUA
    # The conditional "if current == increment" is the crucial part that
    # restricts EXPIRE to the first increment.
    assert "current == increment" in _QUOTA_INCREMENT_LUA
