"""Shared test fixtures."""

from __future__ import annotations

import os
import uuid

import pytest


# Set test environment variables before any imports
os.environ["APP_ENV"] = "development"
os.environ["APP_SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_platform_test"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["LITELLM_API_BASE"] = "http://localhost:4000"
os.environ["LITELLM_MASTER_KEY"] = "sk-test-key"


@pytest.fixture
def tenant_id() -> uuid.UUID:
    """Fixed tenant ID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def app_id() -> uuid.UUID:
    """Fixed app ID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def sample_messages() -> list[dict]:
    """Sample conversation messages."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "The capital of France is Paris."},
        {"role": "user", "content": "What about Germany?"},
    ]


@pytest.fixture
def sample_pdf_content() -> bytes:
    """Minimal PDF content for testing."""
    # This is a minimal valid PDF
    return b"%PDF-1.0\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
