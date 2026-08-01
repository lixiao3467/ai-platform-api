"""Soft delete mixin — adds soft delete capability to models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class SoftDeleteMixin:
    """
    Mixin for models that support soft delete.

    Adds a `deleted_at` column. Records are never physically deleted;
    instead, `deleted_at` is set to the current timestamp.

    Query filtering:
        Use `.where(Model.deleted_at.is_(None))` to exclude deleted records.
        Or use the `active_only` class method if implemented on the repository.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Mark this record as deleted."""
        self.deleted_at = func.now()

    def restore(self) -> None:
        """Restore a soft-deleted record."""
        self.deleted_at = None
