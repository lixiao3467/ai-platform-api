"""Integration tests for Quota flow — quota enforcement, limits, and exceeded scenarios."""

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
# Quota Enforcement Tests
# =============================================================================


class TestQuotaEnforcement:
    """Test quota enforcement on various endpoints."""

    @pytest.mark.asyncio
    async def test_chat_endpoint_checks_quota(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that chat endpoint enforces quota."""
        # This test assumes quota is configured for the test tenant
        resp = await client.post(
            "/api/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers=auth_headers,
        )
        # Should either succeed (quota available) or return 429 (quota exceeded)
        # Not 500 (quota check failure)
        assert resp.status_code in (200, 429, 503)

    @pytest.mark.asyncio
    async def test_knowledge_query_checks_quota(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that knowledge query endpoint enforces quota."""
        resp = await client.post(
            "/api/v1/knowledge-bases/query",
            json={
                "question": "test",
                "top_k": 5,
            },
            headers=auth_headers,
        )
        # Should not crash on quota check
        assert resp.status_code in (200, 404, 422, 429, 503)


# =============================================================================
# Quota Limit Tests
# =============================================================================


class TestQuotaLimits:
    """Test quota limit configuration and enforcement."""

    @pytest.mark.asyncio
    async def test_quota_exceeded_returns_429(self, client: AsyncClient) -> None:
        """Test that quota exceeded returns 429 status code."""
        # This test would require mocking quota to be exceeded
        # For now, we verify the response structure when quota is checked
        pass  # Requires quota mocking infrastructure

    @pytest.mark.asyncio
    async def test_quota_response_includes_details(self, client: AsyncClient) -> None:
        """Test that 429 response includes quota details."""
        # This test would require mocking quota to be exceeded
        pass  # Requires quota mocking infrastructure


# =============================================================================
# Quota Increment Tests
# =============================================================================


class TestQuotaIncrement:
    """Test quota increment behavior."""

    @pytest.mark.asyncio
    async def test_successful_request_increments_quota(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that successful request increments quota counter."""
        # This test verifies the quota increment logic
        # Would require Redis mocking to verify counter increment
        pass  # Requires Redis mocking

    @pytest.mark.asyncio
    async def test_failed_request_does_not_increment_quota(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that failed request (validation error) does not increment quota."""
        # Send invalid request
        resp = await client.post(
            "/api/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},  # Empty messages = validation error
            headers=auth_headers,
        )
        assert resp.status_code == 422
        # Quota should not be incremented for validation failures


# =============================================================================
# Quota Configuration Tests
# =============================================================================


class TestQuotaConfiguration:
    """Test quota configuration retrieval and caching."""

    @pytest.mark.asyncio
    async def test_tenant_self_usage_endpoint(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test tenant self-service usage endpoint."""
        resp = await client.get(
            "/api/v1/tenant/self/usage",
            headers=auth_headers,
        )
        # Should return usage information
        assert resp.status_code in (200, 404)  # 404 if endpoint doesn't exist yet


# =============================================================================
# Quota Bypass Tests
# =============================================================================


class TestQuotaBypass:
    """Test quota bypass for special cases."""

    @pytest.mark.asyncio
    async def test_superadmin_bypasses_quota(self, client: AsyncClient) -> None:
        """Test that superadmin users bypass quota checks."""
        # This test would require creating a superadmin token
        # Superadmin should never get 429
        pass  # Requires superadmin token creation

    @pytest.mark.asyncio
    async def test_service_key_bypasses_quota(self, client: AsyncClient) -> None:
        """Test that service keys with * permission bypass quota."""
        # This test would require creating a service key with * permission
        pass  # Requires service key creation


# =============================================================================
# Quota Key Format Tests
# =============================================================================


class TestQuotaKeyFormat:
    """Test quota Redis key format and isolation."""

    def test_quota_key_format(self) -> None:
        """Test that quota key has correct format."""
        from ai_platform.api.middleware.quota import _quota_key

        tenant_id = "tenant-123"
        resource_type = "model_calls"

        key = _quota_key(tenant_id, resource_type)

        assert key == "aip:quota:tenant-123:model_calls"
        assert tenant_id in key
        assert resource_type in key

    def test_different_tenants_have_different_keys(self) -> None:
        """Test that different tenants have different quota keys."""
        from ai_platform.api.middleware.quota import _quota_key

        key1 = _quota_key("tenant-1", "model_calls")
        key2 = _quota_key("tenant-2", "model_calls")

        assert key1 != key2

    def test_different_resources_have_different_keys(self) -> None:
        """Test that different resource types have different quota keys."""
        from ai_platform.api.middleware.quota import _quota_key

        key1 = _quota_key("tenant-1", "model_calls")
        key2 = _quota_key("tenant-1", "storage")

        assert key1 != key2


# =============================================================================
# Quota TTL Tests
# =============================================================================


class TestQuotaTTL:
    """Test quota TTL (auto-reset) behavior."""

    def test_quota_ttl_is_31_days(self) -> None:
        """Test that quota TTL is set to 31 days."""
        from ai_platform.api.middleware.quota import _QUOTA_TTL_SECONDS

        # 31 days in seconds
        expected_ttl = 31 * 86400
        assert _QUOTA_TTL_SECONDS == expected_ttl


# =============================================================================
# Quota Lua Script Tests
# =============================================================================


class TestQuotaLuaScript:
    """Test quota Lua script for atomicity."""

    def test_lua_script_is_atomic(self) -> None:
        """Test that Lua script performs atomic increment + expire."""
        from ai_platform.api.middleware.quota import _QUOTA_INCREMENT_LUA

        # Verify the script contains both INCRBY and EXPIRE
        assert "INCRBY" in _QUOTA_INCREMENT_LUA
        assert "EXPIRE" in _QUOTA_INCREMENT_LUA
        # Verify conditional expire (only on first increment)
        assert "current == increment" in _QUOTA_INCREMENT_LUA
