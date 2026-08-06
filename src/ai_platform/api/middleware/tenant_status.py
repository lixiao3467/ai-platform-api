"""Tenant status middleware — blocks requests from suspended/cancelled tenants.

The actual check is performed inside ``get_request_context()`` (auth.py)
so that every authenticated request is automatically gated.  This module
re-exports the helper for use in tests and custom middleware stacks.
"""

from __future__ import annotations

import uuid

from ai_platform.api.middleware.auth import (
    _check_tenant_status,
    invalidate_tenant_status_cache,
)


async def check_tenant_active(tenant_id: uuid.UUID) -> str:
    """Verify that the tenant is active.

    Returns the status string on success.
    Raises ``HTTPException(403)`` if the tenant is suspended / cancelled.
    Raises ``HTTPException(404)`` if the tenant does not exist.

    Results are cached in Redis for 5 minutes.
    """
    return await _check_tenant_status(tenant_id)


__all__ = [
    "check_tenant_active",
    "invalidate_tenant_status_cache",
]
