"""Tests for configuration management."""

from ai_platform.config import AppSettings


def test_default_settings() -> None:
    """Settings should load with sensible defaults."""
    settings = AppSettings()
    assert settings.app_name == "ai-platform"
    assert settings.app_env == "development"
    assert settings.is_development is True
    assert settings.is_production is False
    assert settings.app_port == 8000
    assert settings.rate_limit_default == 1000
    assert settings.jwt_algorithm == "HS256"


def test_milvus_uri_configured_directly() -> None:
    """milvus_uri is a plain string field that can be overridden directly."""
    settings = AppSettings(milvus_uri="http://myhost:19530")
    assert settings.milvus_uri == "http://myhost:19530"


def test_production_detection() -> None:
    """is_production should be True when app_env is production."""
    settings = AppSettings(app_env="production")
    assert settings.is_production is True
    assert settings.is_development is False


# ---------------------------------------------------------------------------
# CORS origin parsing (Phase 0 — Task 1)
# ---------------------------------------------------------------------------


def test_cors_origins_empty_returns_reject_all() -> None:
    """An empty CORS_ALLOWED_ORIGINS value must mean 'reject all'."""
    settings = AppSettings(cors_allowed_origins="")
    assert settings.cors_origins == []


def test_cors_origins_whitespace_only_returns_reject_all() -> None:
    """Whitespace-only must be treated the same as empty."""
    settings = AppSettings(cors_allowed_origins="   \t ")
    assert settings.cors_origins == []


def test_cors_origins_single_value() -> None:
    settings = AppSettings(cors_allowed_origins="https://app.example.com")
    assert settings.cors_origins == ["https://app.example.com"]


def test_cors_origins_multiple_stripped() -> None:
    """Each entry is stripped; empty tokens are dropped."""
    settings = AppSettings(
        cors_allowed_origins=" https://a.com , https://b.com, ,https://c.com  "
    )
    assert settings.cors_origins == [
        "https://a.com",
        "https://b.com",
        "https://c.com",
    ]


def test_cors_origins_trailing_comma_ignored() -> None:
    settings = AppSettings(cors_allowed_origins="https://a.com,")
    assert settings.cors_origins == ["https://a.com"]

