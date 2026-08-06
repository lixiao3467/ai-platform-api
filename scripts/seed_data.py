"""Seed script — initialize default permissions, roles, and admin user."""

from __future__ import annotations

import asyncio
import uuid

import bcrypt

from ai_platform.domain.models import Permission, Role, Tenant, User
from ai_platform.infra.database.connection import get_session_factory, init_db

# =============================================================================
# Default Permissions — all resources × CRUD actions
# =============================================================================

RESOURCES = [
    ("chat", "对话"),
    ("conversation", "会话"),
    ("knowledge_base", "知识库"),
    ("document", "文档"),
    ("agent", "Agent"),
    ("tool", "工具"),
    ("workflow", "工作流"),
    ("prompt", "Prompt 模板"),
    ("model_provider", "模型提供商"),
    ("evaluation", "评测"),
    ("cost", "成本"),
    ("user", "用户"),
    ("role", "角色"),
    ("tenant", "租户"),
    ("audit_log", "审计日志"),
]

ACTIONS = [
    ("create", "创建"),
    ("read", "查看"),
    ("update", "编辑"),
    ("delete", "删除"),
    ("execute", "执行"),
]

# Default system roles — 5 roles, 2-tier hierarchy
# Platform tier: super_admin, platform_ops
# Tenant tier: tenant_admin, tenant_developer, tenant_viewer
SYSTEM_ROLES = [
    {
        "name": "超级管理员",
        "code": "super_admin",
        "description": "平台最高权限，管理所有资源、角色和系统配置",
        "is_system": True,
        "permissions": "all",  # All 75 permissions
    },
    {
        "name": "平台运营员",
        "code": "platform_ops",
        "description": "管理租户、模型、成本分析、用户和审计日志",
        "is_system": True,
        "permissions": "platform_ops",
    },
    {
        "name": "租户管理员",
        "code": "tenant_admin",
        "description": "租户内最高权限，管理成员和全部AI能力",
        "is_system": True,
        "permissions": "tenant_admin",
    },
    {
        "name": "租户开发者",
        "code": "tenant_developer",
        "description": "AI能力调用、Prompt/知识库/Agent/Workflow管理",
        "is_system": True,
        "permissions": "tenant_developer",
    },
    {
        "name": "租户观察者",
        "code": "tenant_viewer",
        "description": "只读查看对话记录、用量和配置",
        "is_system": True,
        "permissions": "tenant_viewer",
    },
]

# Permission sets by role code (used as lookup keys)
# "all" = all 75 permissions
# Named sets are defined below for clarity
ROLE_PERMISSION_MAP: dict[str, str | list[tuple[str, str]]] = {
    "super_admin": "all",
    "platform_ops": "platform_ops",
    "tenant_admin": "tenant_admin",
    "tenant_developer": "tenant_developer",
    "tenant_viewer": "tenant_viewer",
}

# Named permission sets — matches init_rbac.sql matrix exactly
def _get_permission_set(role_code: str) -> list[tuple[str, str]]:
    """Return the list of (resource, action) tuples for a named role."""
    if role_code == "platform_ops":
        # Platform management CRUD + all resources read-only
        perms: list[tuple[str, str]] = []
        # Full CRUD on platform management resources
        for resource in ("tenant", "model_provider", "cost", "evaluation", "user"):
            for action in ("create", "read", "update", "delete", "execute"):
                perms.append((resource, action))
        # Read-only on everything else
        for resource, _ in RESOURCES:
            if resource not in ("tenant", "model_provider", "cost", "evaluation", "user"):
                perms.append((resource, "read"))
        return perms

    elif role_code == "tenant_admin":
        perms = []
        # Full CRUD on AI capability resources
        for resource in (
            "chat", "conversation", "knowledge_base", "document",
            "agent", "tool", "workflow", "prompt",
        ):
            for action in ("create", "read", "update", "delete", "execute"):
                perms.append((resource, action))
        # model_provider: read + execute
        perms.extend([("model_provider", "read"), ("model_provider", "execute")])
        # evaluation: full CRUD + execute
        for action in ("create", "read", "update", "delete", "execute"):
            perms.append(("evaluation", action))
        # cost: read only
        perms.append(("cost", "read"))
        # user: create + read + update
        perms.extend([("user", "create"), ("user", "read"), ("user", "update")])
        # role, tenant, audit_log: read only
        for resource in ("role", "tenant", "audit_log"):
            perms.append((resource, "read"))
        return perms

    elif role_code == "tenant_developer":
        perms = []
        # AI resources: create/read/update/execute (no delete)
        for resource in (
            "chat", "conversation", "knowledge_base",
            "agent", "tool", "workflow", "prompt",
        ):
            for action in ("create", "read", "update", "execute"):
                perms.append((resource, action))
        # document: create/read/update only
        for action in ("create", "read", "update"):
            perms.append(("document", action))
        # model_provider: read + execute
        perms.extend([("model_provider", "read"), ("model_provider", "execute")])
        # evaluation: create/read/update/execute (no delete)
        for action in ("create", "read", "update", "execute"):
            perms.append(("evaluation", action))
        # Management resources: read only
        for resource in ("cost", "user", "role", "tenant", "audit_log"):
            perms.append((resource, "read"))
        return perms

    elif role_code == "tenant_viewer":
        # All resources: read only
        return [(r, "read") for r, _ in RESOURCES]

    return []


async def seed() -> None:
    """Initialize the database with default data."""
    await init_db()
    factory = get_session_factory()

    async with factory() as session:
        # --- Create default tenant ---
        tenant = Tenant(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            name="默认租户",
            slug="default",
            plan="enterprise",
        )
        session.add(tenant)
        await session.flush()
        print(f"[OK] Tenant: {tenant.name} ({tenant.id})")

        # --- Create permissions ---
        permissions: dict[tuple[str, str], Permission] = {}
        for resource, resource_label in RESOURCES:
            for action, action_label in ACTIONS:
                perm = Permission(
                    id=uuid.uuid4(),
                    resource=resource,
                    action=action,
                    description=f"{action_label}{resource_label}",
                )
                session.add(perm)
                permissions[(resource, action)] = perm

        await session.flush()
        print(f"[OK] Permissions: {len(permissions)} created")

        # --- Create system roles ---
        roles: dict[str, Role] = {}
        for role_def in SYSTEM_ROLES:
            role = Role(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                name=role_def["name"],
                description=role_def["description"],
                is_system=role_def["is_system"],
            )
            # Set the code attribute if the model supports it
            if hasattr(role, "code"):
                role.code = role_def["code"]

            perm_key = role_def.get("permissions", role_def["code"])
            if perm_key == "all":
                role.permissions = list(permissions.values())
            else:
                perm_tuples = _get_permission_set(role_def["code"])
                role.permissions = [
                    permissions[(r, a)]
                    for r, a in perm_tuples
                    if (r, a) in permissions
                ]

            session.add(role)
            roles[role_def["name"]] = role
            roles[role_def["code"]] = role  # Also index by code

        await session.flush()
        role_summary = ", ".join(
            f"{r['name']}({r['code']})" for r in SYSTEM_ROLES
        )
        print(f"[OK] Roles: {len(SYSTEM_ROLES)} created ({role_summary})")

        # --- Create admin user ---
        admin = User(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            username="admin",
            email="admin@ai-platform.local",
            password_hash=bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode(),
            display_name="系统管理员",
            is_active=True,
            is_superadmin=True,
        )
        admin.roles = [roles["超级管理员"]]
        session.add(admin)

        await session.commit()
        print(f"[OK] Admin user: admin / admin123 ({admin.id})")

    print("\n[SUCCESS] Seed complete! Login with admin / admin123")


if __name__ == "__main__":
    asyncio.run(seed())
