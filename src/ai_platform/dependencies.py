"""FastAPI dependency injection providers."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.infra.database.connection import get_db


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async database session."""
    async with get_db() as session:
        yield session


# Convenience re-exports for route handlers
__all__ = [
    "AsyncSession",
    "Depends",
    "RequestContext",
    "get_request_context",
    "get_session",
]
