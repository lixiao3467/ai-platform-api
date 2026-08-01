"""Unit of Work pattern — explicit transaction boundaries."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.infra.database.connection import get_session_factory

logger = structlog.get_logger()


class UnitOfWork:
    """
    Unit of Work — manages a single database transaction boundary.

    Usage:
        async with UnitOfWork() as uow:
            repo = ConversationRepository(uow.session)
            conv = await repo.create(...)
            await repo.update(conv, title="New Title")
            # Auto-committed on successful exit
            # Auto-rolled back on exception

    For complex operations spanning multiple repositories:
        async with UnitOfWork() as uow:
            conv_repo = ConversationRepository(uow.session)
            msg_repo = MessageRepository(uow.session)
            conv = await conv_repo.create(...)
            msg = await msg_repo.create(conversation_id=conv.id, ...)
            # Both committed atomically
    """

    def __init__(self) -> None:
        self._session: AsyncSession | None = None
        self._committed = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork not entered — use 'async with UnitOfWork()'")
        return self._session

    async def __aenter__(self) -> UnitOfWork:
        factory = get_session_factory()
        self._session = factory()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._session is None:
            return

        try:
            if exc_type is not None:
                # Exception occurred — rollback
                await self._session.rollback()
                logger.debug("UnitOfWork rolled back", error=str(exc_val))
            elif not self._committed:
                # No exception, not explicitly committed — auto-commit
                await self._session.commit()
                logger.debug("UnitOfWork auto-committed")
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        """Explicitly commit the transaction."""
        if self._session is None:
            raise RuntimeError("UnitOfWork not entered")
        await self._session.commit()
        self._committed = True
        logger.debug("UnitOfWork explicitly committed")

    async def rollback(self) -> None:
        """Explicitly rollback the transaction."""
        if self._session is None:
            raise RuntimeError("UnitOfWork not entered")
        await self._session.rollback()
        self._committed = True  # Prevent auto-commit on exit
        logger.debug("UnitOfWork explicitly rolled back")

    async def flush(self) -> None:
        """Flush pending changes to the database (without committing)."""
        if self._session is None:
            raise RuntimeError("UnitOfWork not entered")
        await self._session.flush()


@asynccontextmanager
async def transaction() -> AsyncGenerator[UnitOfWork, None]:
    """
    Convenience context manager for transactions.

    Usage:
        async with transaction() as uow:
            repo = MyRepository(uow.session)
            ...
    """
    async with UnitOfWork() as uow:
        yield uow
