"""Common response schemas."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Standard API response wrapper."""

    code: int = Field(default=0, description="0 = success, non-zero = error")
    data: T | None = None
    message: str = "ok"


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated list response."""

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size


class ErrorResponse(BaseModel):
    """Error response."""

    code: int
    message: str
    detail: Any | None = None
