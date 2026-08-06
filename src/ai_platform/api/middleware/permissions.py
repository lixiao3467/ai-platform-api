"""RBAC permission enforcement dependency."""

from __future__ import annotations

from fastapi import Depends, HTTPException

from ai_platform.api.middleware.auth import RequestContext, get_request_context

# Mapping from new-style permissions to legacy dot-notation strings
# stored in api_keys.permissions JSON column.
#
# When a requirement is stated as "agent.manage", we also accept
# the legacy alias "agent.write" (and vice-versa) so that existing
# API keys and role assignments keep working.
_LEGACY_ALIASES: dict[str, set[str]] = {
    "agent.manage": {"agent.write", "agent.manage"},
    "knowledge.manage": {"knowledge.write", "knowledge.manage"},
    "workflow.manage": {"workflow.write", "workflow.manage"},
    "user.manage": {"user.update", "user.delete", "user.manage"},
    "apikey.manage": {"apikey.manage"},
    "prompt.manage": {"prompt.write", "prompt.manage"},
    "tool.manage": {"tool.write", "tool.manage"},
    # Read aliases — any write/manage implies read
    "agent.read": {"agent.read", "agent.write", "agent.manage"},
    "knowledge.read": {"knowledge.read", "knowledge.write", "knowledge.manage"},
    "workflow.read": {"workflow.read", "workflow.write", "workflow.manage"},
    "app.read": {"app.read", "app.write", "app.manage"},
    "prompt.read": {"prompt.read", "prompt.write", "prompt.manage"},
    "tool.read": {"tool.read", "tool.write", "tool.manage"},
    "model.read": {"model.read", "model.write", "model.manage"},
    "cost.read": {"cost.read", "cost.manage"},
    "evaluation.read": {"evaluation.read", "evaluation.manage"},
    "metric.read": {"metric.read", "metric.manage"},
    "audit.view": {"audit.view", "audit.read", "audit:view"},
    "audit:view": {"audit.view", "audit.read", "audit:view"},
    "tenant:config": {"tenant:config", "tenant.config"},
    "tenant:quota_view": {"tenant:quota_view", "tenant.quota_view"},
}


def _perm_matches(held: str, required: str) -> bool:
    """Check if a held permission satisfies a required permission.

    Handles both colon-separated (``agent:manage``) and dot-separated
    (``agent.write``) formats, plus legacy alias expansion.
    """
    if held == required:
        return True

    # Normalize: treat "agent:manage" and "agent.manage" as equivalent
    # only when they appear in the alias set for the required perm.
    aliases = _LEGACY_ALIASES.get(required)
    if aliases and held in aliases:
        return True

    # Also try the reverse — if held is in the alias map and required
    # is one of its aliases.
    held_aliases = _LEGACY_ALIASES.get(held)
    if held_aliases and required in held_aliases:
        return True

    return False


def require_permission(*required_perms: str):
    """FastAPI dependency that enforces one or more RBAC permissions.

    Usage::

        @router.delete(
            "/{user_id}",
            dependencies=[Depends(require_permission("user.delete"))],
        )
        async def delete_user(...):
            ...

    A context permission of ``"*"`` is treated as a super-permission that
    satisfies any requirement (used for service-to-service / admin keys
    and platform super-admins).
    """

    async def checker(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
        # Super-permission bypass — service keys or super-admins
        if "*" in ctx.permissions or ctx.is_superadmin:
            return ctx

        for required in required_perms:
            satisfied = any(_perm_matches(held, required) for held in ctx.permissions)
            if not satisfied:
                raise HTTPException(
                    status_code=403,
                    detail=f"Missing permission: {required}",
                )
        return ctx

    return checker
