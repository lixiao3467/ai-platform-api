"""Base repository with common CRUD operations."""

from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_platform.domain.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Generic async repository with common operations."""

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def get_by_id(self, id: uuid.UUID) -> ModelT | None:
        """Fetch a single record by primary key."""
        return await self._session.get(self._model, id)

    async def get_many(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        order_by: str = "created_at",
    ) -> list[ModelT]:
        """Fetch a paginated list."""
        stmt = (
            select(self._model)
            .order_by(getattr(self._model, order_by).desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **kwargs) -> ModelT:  # type: ignore[no-untyped-def]
        """Create a new record."""
        instance = self._model(**kwargs)
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def update(self, instance: ModelT, **kwargs) -> ModelT:  # type: ignore[no-untyped-def]
        """Update an existing record."""
        for key, value in kwargs.items():
            setattr(instance, key, value)
        await self._session.flush()
        return instance

    async def delete(self, instance: ModelT) -> None:
        """Soft or hard delete a record."""
        await self._session.delete(instance)
        await self._session.flush()
