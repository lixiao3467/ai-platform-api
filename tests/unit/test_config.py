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


def test_milvus_uri_computed() -> None:
    """milvus_uri should be computed from host and port."""
    settings = AppSettings(milvus_host="myhost", milvus_port=19530)
    assert settings.milvus_uri == "http://myhost:19530"


def test_production_detection() -> None:
    """is_production should be True when app_env is production."""
    settings = AppSettings(app_env="production")
    assert settings.is_production is True
    assert settings.is_development is False
