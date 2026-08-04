"""Database connection management with async SQLAlchemy."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_platform.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _normalize_database_url(url: str) -> tuple[str, dict]:
    """
    Normalize DATABASE_URL for asyncpg compatibility.

    asyncpg uses `ssl=require|prefer|allow|disable` instead of psycopg2's
    `sslmode=require`. Also returns connect_args for explicit SSL control.
    """
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    connect_args: dict = {}

    # Convert sslmode (psycopg2) to ssl (asyncpg)
    if "sslmode" in query and "ssl" not in query:
        sslmode = query.pop("sslmode")[0]
        # Map psycopg2 sslmode values to asyncpg ssl values
        ssl_map = {"require": "require", "prefer": "prefer", "allow": "prefer",
                    "disable": "disable", "verify-ca": "require",
                    "verify-full": "require"}
        connect_args["ssl"] = ssl_map.get(sslmode, "require")
        # Rebuild URL without sslmode
        new_query = urlencode(query, doseq=True)
        parsed = parsed._replace(query=new_query)
        url = urlunparse(parsed)

    return url, connect_args


def get_engine() -> AsyncEngine:
    """Get or create the async engine singleton."""
    global _engine
    if _engine is None:
        settings = get_settings()
        url, connect_args = _normalize_database_url(settings.database_url)
        _engine = create_async_engine(
            url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            pool_recycle=300,  # Recycle connections every 5 min to avoid stale TCP
            echo=settings.app_debug,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the session factory singleton."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: yields an async database session.

    Auto-commits on success, rolls back on error, always closes the session.
    Handles asyncio.CancelledError (client disconnect) safely.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except asyncio.CancelledError:
            # Client disconnected — rollback any partial work, then re-raise
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Initialize database engine (called on app startup)."""
    get_engine()
    get_session_factory()


async def close_db() -> None:
    """Dispose database engine (called on app shutdown)."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
