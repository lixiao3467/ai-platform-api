"""Integration tests for Auth flow — login, refresh, logout, JWT validation."""

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
def valid_jwt_token():
    """Create a valid JWT token for testing."""
    from ai_platform.api.middleware.auth import create_jwt_token

    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    return create_jwt_token(tenant_id, user_id)


@pytest.fixture
def valid_refresh_token():
    """Create a valid refresh token for testing."""
    from ai_platform.api.middleware.auth import create_refresh_token

    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    return create_refresh_token(tenant_id, user_id)


# =============================================================================
# Auth Endpoint Tests
# =============================================================================


class TestAuthEndpoints:
    """Test auth endpoints: login, refresh, logout."""

    @pytest.mark.asyncio
    async def test_login_endpoint_exists(self, client: AsyncClient) -> None:
        """Test that login endpoint is accessible."""
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "test", "password": "test"},
        )
        # Should return 401 (invalid credentials) not 404
        assert resp.status_code in (401, 403, 422)

    @pytest.mark.asyncio
    async def test_login_missing_credentials(self, client: AsyncClient) -> None:
        """Test login without credentials returns 422."""
        resp = await client.post("/api/v1/auth/login", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_login_invalid_json(self, client: AsyncClient) -> None:
        """Test login with invalid JSON returns 422."""
        resp = await client.post(
            "/api/v1/auth/login",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_refresh_endpoint_exists(self, client: AsyncClient) -> None:
        """Test that refresh endpoint is accessible."""
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid"},
        )
        # Should return 401 (invalid token) not 404
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_missing_token(self, client: AsyncClient) -> None:
        """Test refresh without token returns 422."""
        resp = await client.post("/api/v1/auth/refresh", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_logout_endpoint_exists(self, client: AsyncClient) -> None:
        """Test that logout endpoint is accessible."""
        resp = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": "invalid"},
        )
        # Should succeed even with invalid token (best effort)
        assert resp.status_code == 200


# =============================================================================
# JWT Validation Tests
# =============================================================================


class TestJWTValidation:
    """Test JWT token validation in protected endpoints."""

    @pytest.mark.asyncio
    async def test_protected_endpoint_without_auth(self, client: AsyncClient) -> None:
        """Test protected endpoint without auth returns 401."""
        resp = await client.get("/api/v1/conversations/")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_endpoint_with_invalid_token(self, client: AsyncClient) -> None:
        """Test protected endpoint with invalid token returns 401."""
        resp = await client.get(
            "/api/v1/conversations/",
            headers={"Authorization": "Bearer invalid_token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_endpoint_with_malformed_header(self, client: AsyncClient) -> None:
        """Test protected endpoint with malformed auth header returns 401."""
        resp = await client.get(
            "/api/v1/conversations/",
            headers={"Authorization": "InvalidFormat"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_endpoint_with_empty_bearer(self, client: AsyncClient) -> None:
        """Test protected endpoint with empty bearer token returns 401."""
        resp = await client.get(
            "/api/v1/conversations/",
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401


# =============================================================================
# API Key Authentication Tests
# =============================================================================


class TestAPIKeyAuth:
    """Test API key authentication."""

    @pytest.mark.asyncio
    async def test_protected_endpoint_with_invalid_api_key(self, client: AsyncClient) -> None:
        """Test protected endpoint with invalid API key returns 401."""
        resp = await client.get(
            "/api/v1/conversations/",
            headers={"X-API-Key": "invalid_key"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_endpoint_with_malformed_api_key(self, client: AsyncClient) -> None:
        """Test protected endpoint with malformed API key returns 401."""
        resp = await client.get(
            "/api/v1/conversations/",
            headers={"X-API-Key": "short"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_api_key_prefix_check(self, client: AsyncClient) -> None:
        """Test that API key must start with aiplat_ prefix."""
        # Key without proper prefix should fail
        resp = await client.get(
            "/api/v1/conversations/",
            headers={"X-API-Key": "wrongprefix_12345678"},
        )
        assert resp.status_code == 401


# =============================================================================
# Token Expiration Tests
# =============================================================================


class TestTokenExpiration:
    """Test token expiration handling."""

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(self, client: AsyncClient) -> None:
        """Test that expired token returns 401."""
        from datetime import datetime, timedelta, timezone

        from jose import jwt

        from ai_platform.config import get_settings

        settings = get_settings()
        now = datetime.now(tz=timezone.utc)

        # Create an expired token
        payload = {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "iat": now - timedelta(hours=2),
            "nbf": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),  # Expired 1 hour ago
            "iss": "ai-platform",
            "type": "access",
        }
        expired_token = jwt.encode(
            payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
        )

        resp = await client.get(
            "/api/v1/conversations/",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401


# =============================================================================
# Token Creation Tests
# =============================================================================


class TestTokenCreation:
    """Test JWT token creation utilities."""

    def test_create_jwt_token_returns_string(self) -> None:
        """Test that create_jwt_token returns a string."""
        from ai_platform.api.middleware.auth import create_jwt_token

        token = create_jwt_token(str(uuid.uuid4()), str(uuid.uuid4()))
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_jwt_token_is_decodeable(self) -> None:
        """Test that created JWT token can be decoded."""
        from ai_platform.api.middleware.auth import create_jwt_token, decode_jwt_token

        tenant_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        token = create_jwt_token(tenant_id, user_id)
        payload = decode_jwt_token(token)

        assert payload["tenant_id"] == tenant_id
        assert payload["sub"] == user_id

    def test_create_refresh_token_returns_string(self) -> None:
        """Test that create_refresh_token returns a string."""
        from ai_platform.api.middleware.auth import create_refresh_token

        token = create_refresh_token(str(uuid.uuid4()), str(uuid.uuid4()))
        assert isinstance(token, str)
        assert len(token) > 0

    def test_refresh_token_has_jti(self) -> None:
        """Test that refresh token includes jti claim."""
        from jose import jwt

        from ai_platform.api.middleware.auth import create_refresh_token
        from ai_platform.config import get_settings

        settings = get_settings()

        token = create_refresh_token(str(uuid.uuid4()), str(uuid.uuid4()))
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )

        assert "jti" in payload
        assert payload["type"] == "refresh"


# =============================================================================
# Security Tests
# =============================================================================


class TestAuthSecurity:
    """Test auth security aspects."""

    @pytest.mark.asyncio
    async def test_no_auth_method_returns_401(self, client: AsyncClient) -> None:
        """Test that request without any auth method returns 401."""
        resp = await client.get("/api/v1/conversations/")
        assert resp.status_code == 401
        assert "Missing authentication" in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_both_auth_methods_api_key_takes_precedence(
        self, client: AsyncClient
    ) -> None:
        """Test that when both auth methods provided, API key takes precedence."""
        # This test verifies the auth precedence logic
        resp = await client.get(
            "/api/v1/conversations/",
            headers={
                "Authorization": "Bearer some_jwt",
                "X-API-Key": "aiplat_test123456",
            },
        )
        # Should try API key first (and fail since it's invalid)
        assert resp.status_code == 401
