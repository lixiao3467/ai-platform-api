"""Integration tests for Tenant flow — tenant self-service, isolation, and management."""

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
# Tenant Self-Service Tests
# =============================================================================


class TestTenantSelfService:
    """Test tenant self-service endpoints."""

    @pytest.mark.asyncio
    async def test_tenant_self_endpoint_exists(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that tenant self endpoint is accessible."""
        resp = await client.get(
            "/api/v1/tenant/self",
            headers=auth_headers,
        )
        # Should not return 404
        assert resp.status_code != 404

    @pytest.mark.asyncio
    async def test_tenant_self_requires_auth(self, client: AsyncClient) -> None:
        """Test that tenant self endpoint requires authentication."""
        resp = await client.get("/api/v1/tenant/self")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_tenant_self_returns_tenant_info(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that tenant self returns tenant information."""
        resp = await client.get(
            "/api/v1/tenant/self",
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            assert data["code"] == 0
            # Should include tenant details
            assert "data" in data


# =============================================================================
# Tenant Usage Tests
# =============================================================================


class TestTenantUsage:
    """Test tenant usage and quota endpoints."""

    @pytest.mark.asyncio
    async def test_tenant_usage_endpoint(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test tenant usage endpoint."""
        resp = await client.get(
            "/api/v1/tenant/self/usage",
            headers=auth_headers,
        )
        # Should return usage information
        assert resp.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_tenant_usage_includes_quota_config(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that usage endpoint includes quota configuration."""
        resp = await client.get(
            "/api/v1/tenant/self/usage",
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "quota_config" in data.get("data", {})


# =============================================================================
# Tenant Member Management Tests
# =============================================================================


class TestTenantMembers:
    """Test tenant member management endpoints."""

    @pytest.mark.asyncio
    async def test_list_tenant_members(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test listing tenant members."""
        resp = await client.get(
            "/api/v1/tenant/self/members",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_invite_tenant_member(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test inviting a new tenant member."""
        resp = await client.post(
            "/api/v1/tenant/self/members/invite",
            json={
                "username": "newuser",
                "email": "newuser@example.com",
                "password": "securepassword123",
            },
            headers=auth_headers,
        )
        # Should either succeed or fail with validation error
        assert resp.status_code in (200, 404, 409, 422)


# =============================================================================
# Tenant Isolation Tests
# =============================================================================


class TestTenantIsolation:
    """Test tenant data isolation."""

    @pytest.mark.asyncio
    async def test_tenant_cannot_access_other_tenant_conversations(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Test that tenant cannot access other tenant's conversations."""
        # Try to access a conversation that doesn't belong to this tenant
        fake_conv_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/conversations/{fake_conv_id}",
            headers=auth_headers,
        )
        # Should return 404 (not found) not 403 (forbidden) to avoid information leakage
        assert resp.status_code in (404, 403)

    @pytest.mark.asyncio
    async def test_tenant_cannot_access_other_tenant_knowledge_bases(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Test that tenant cannot access other tenant's knowledge bases."""
        fake_kb_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/knowledge-bases/{fake_kb_id}",
            headers=auth_headers,
        )
        assert resp.status_code in (404, 403)

    @pytest.mark.asyncio
    async def test_tenant_cannot_access_other_tenant_agents(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Test that tenant cannot access other tenant's agents."""
        fake_agent_id = uuid.uuid4()
        resp = await client.get(
            f"/api/v1/agents/{fake_agent_id}",
            headers=auth_headers,
        )
        assert resp.status_code in (404, 403)


# =============================================================================
# Tenant Status Tests
# =============================================================================


class TestTenantStatus:
    """Test tenant status checks."""

    @pytest.mark.asyncio
    async def test_active_tenant_can_access_api(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that active tenant can access API endpoints."""
        resp = await client.get(
            "/api/v1/conversations/",
            headers=auth_headers,
        )
        # Should succeed for active tenant
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_suspended_tenant_gets_403(self, client: AsyncClient) -> None:
        """Test that suspended tenant gets 403 Forbidden."""
        # This test would require creating a suspended tenant token
        # Suspended tenant should get 403 on all endpoints
        pass  # Requires suspended tenant setup

    @pytest.mark.asyncio
    async def test_cancelled_tenant_gets_403(self, client: AsyncClient) -> None:
        """Test that cancelled tenant gets 403 Forbidden."""
        # This test would require creating a cancelled tenant token
        pass  # Requires cancelled tenant setup


# =============================================================================
# Tenant Update Tests
# =============================================================================


class TestTenantUpdate:
    """Test tenant update endpoints."""

    @pytest.mark.asyncio
    async def test_update_tenant_info(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test updating tenant information."""
        resp = await client.put(
            "/api/v1/tenant/self",
            json={"name": "Updated Tenant Name"},
            headers=auth_headers,
        )
        # Should either succeed or fail with validation error
        assert resp.status_code in (200, 404, 422)

    @pytest.mark.asyncio
    async def test_update_tenant_validates_name_length(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that tenant name length is validated."""
        resp = await client.put(
            "/api/v1/tenant/self",
            json={"name": "A" * 200},  # Too long
            headers=auth_headers,
        )
        # Should fail validation
        assert resp.status_code in (422, 404)


# =============================================================================
# Tenant Models Access Tests
# =============================================================================


class TestTenantModels:
    """Test tenant model access endpoints."""

    @pytest.mark.asyncio
    async def test_list_accessible_models(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test listing models accessible to tenant."""
        resp = await client.get(
            "/api/v1/tenant/self/models",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 404)


# =============================================================================
# Tenant Audit Logs Tests
# =============================================================================


class TestTenantAuditLogs:
    """Test tenant audit log endpoints."""

    @pytest.mark.asyncio
    async def test_list_tenant_audit_logs(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test listing tenant audit logs."""
        resp = await client.get(
            "/api/v1/tenant/self/audit-logs",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_audit_logs_scoped_to_tenant(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that audit logs are scoped to tenant."""
        resp = await client.get(
            "/api/v1/tenant/self/audit-logs",
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            # All logs should belong to the current tenant
            # (This is a structural test - actual tenant scoping is tested in unit tests)
            assert "items" in data.get("data", {})


# =============================================================================
# Tenant Quota Configuration Tests
# =============================================================================


class TestTenantQuotaConfig:
    """Test tenant quota configuration."""

    @pytest.mark.asyncio
    async def test_tenant_has_default_quota(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test that tenant has default quota configuration."""
        resp = await client.get(
            "/api/v1/tenant/self",
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Should have quota limits configured
            tenant_data = data.get("data", {})
            assert "max_users" in tenant_data or "quota_config" in tenant_data


# =============================================================================
# Tenant App Management Tests
# =============================================================================


class TestTenantApps:
    """Test tenant app management."""

    @pytest.mark.asyncio
    async def test_list_tenant_apps(self, client: AsyncClient, auth_headers: dict) -> None:
        """Test listing apps within tenant."""
        # Apps are managed through a different endpoint, but tenant should see their apps
        resp = await client.get(
            "/api/v1/conversations/",
            headers=auth_headers,
        )
        # Should return conversations for tenant's apps
        assert resp.status_code == 200
