"""FastAPI dependency injection providers."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.api.middleware.auth import RequestContext, get_request_context
from ai_platform.infra.database.connection import get_db


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async database session.

    Thin wrapper around `get_db` for routes that prefer a dedicated name.
    Usage in routes:
        session: AsyncSession = Depends(get_session)
    """
    async for session in get_db():
        yield session
        return


# Convenience re-exports for route handlers
__all__ = [
    "AsyncSession",
    "Depends",
    "RequestContext",
    "get_request_context",
    "get_session",
]
