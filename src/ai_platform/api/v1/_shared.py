"""Shared request schemas for POST-only API migration."""
from pydantic import BaseModel, field_validator
from uuid import UUID

class IdRequest(BaseModel):
    """Base for endpoints that operate on a single resource by ID."""
    id: str

    @field_validator("id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        UUID(v)  # raises ValueError if invalid
        return v

def validate_uuid(value: str) -> UUID:
    """Validate and parse a UUID string."""
    return UUID(value)
