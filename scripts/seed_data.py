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

# Default system roles
SYSTEM_ROLES = [
    {
        "name": "超级管理员",
        "description": "拥有所有权限，不可删除",
        "is_system": True,
        "permissions": "all",  # All permissions
    },
    {
        "name": "管理员",
        "description": "管理用户、模型、知识库等核心资源",
        "is_system": True,
        "permissions": "all",
    },
    {
        "name": "开发者",
        "description": "使用 AI 能力（对话、Agent、知识库），不能管理用户和系统设置",
        "is_system": True,
        "permissions": [
            # Full access to AI capabilities
            ("chat", "create"), ("chat", "read"),
            ("conversation", "create"), ("conversation", "read"), ("conversation", "delete"),
            ("knowledge_base", "create"), ("knowledge_base", "read"), ("knowledge_base", "update"),
            ("document", "create"), ("document", "read"), ("document", "delete"),
            ("agent", "create"), ("agent", "read"), ("agent", "update"), ("agent", "execute"),
            ("workflow", "create"), ("workflow", "read"), ("workflow", "update"), ("workflow", "execute"),
            ("prompt", "create"), ("prompt", "read"), ("prompt", "update"),
            ("evaluation", "create"), ("evaluation", "read"),
            # Read-only for system resources
            ("model_provider", "read"),
            ("cost", "read"),
            ("user", "read"),
            ("role", "read"),
            ("audit_log", "read"),
        ],
    },
    {
        "name": "观察者",
        "description": "只读权限，可查看但不能修改",
        "is_system": True,
        "permissions": [
            (r, "read") for r, _ in RESOURCES
        ],
    },
]


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

            if role_def["permissions"] == "all":
                role.permissions = list(permissions.values())
            else:
                role.permissions = [
                    permissions[(r, a)]
                    for r, a in role_def["permissions"]
                    if (r, a) in permissions
                ]

            session.add(role)
            roles[role_def["name"]] = role

        await session.flush()
        print(f"[OK] Roles: {len(roles)} created ({', '.join(roles.keys())})")

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
