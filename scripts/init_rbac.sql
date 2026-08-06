-- ============================================================================
-- RBAC 权限角色矩阵初始化脚本
-- 版本: v2.0 — 5角色 + 75权限点 + 完整矩阵
-- 执行环境: PostgreSQL 14+
-- 执行方式: psql -h <host> -U <user> -d <database> -f init_rbac.sql
-- ============================================================================

BEGIN;

-- ────────────────────────────────────────────────────────────────────────────
-- 0. 添加 roles.code 列（如不存在）
--    用于程序化角色识别，避免前端硬编码中文名
-- ────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'roles' AND column_name = 'code'
    ) THEN
        ALTER TABLE roles ADD COLUMN code VARCHAR(64);
        CREATE UNIQUE INDEX idx_roles_code ON roles(code) WHERE code IS NOT NULL;
        RAISE NOTICE '已添加 roles.code 列';
    ELSE
        RAISE NOTICE 'roles.code 列已存在，跳过';
    END IF;
END $$;

-- 确保 (tenant_id, name) 有唯一约束（ON CONFLICT 依赖它）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_roles_tenant_name'
    ) THEN
        ALTER TABLE roles ADD CONSTRAINT uq_roles_tenant_name UNIQUE (tenant_id, name);
        RAISE NOTICE '已添加 uq_roles_tenant_name 唯一约束';
    END IF;
END $$;

-- ────────────────────────────────────────────────────────────────────────────
-- 1. 插入/更新 5 个系统角色
--    使用 ON CONFLICT 确保幂等（基于 tenant_id + name 唯一）
-- ────────────────────────────────────────────────────────────────────────────

-- 默认租户 ID（与 seed_data.py 保持一致）
-- 如果你的租户 ID 不同，请修改此变量
DO $$
DECLARE
    v_tenant_id UUID;
    v_super_admin_id UUID;
    v_platform_ops_id UUID;
    v_tenant_admin_id UUID;
    v_tenant_dev_id UUID;
    v_tenant_viewer_id UUID;
    v_perm_id UUID;
BEGIN
    -- 获取默认租户
    SELECT id INTO v_tenant_id FROM tenants WHERE slug = 'default' LIMIT 1;
    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION '默认租户不存在，请先执行 seed_data.py 或手动创建 default 租户';
    END IF;
    RAISE NOTICE '使用租户: % (%)', v_tenant_id, 'default';

    -- ── 插入/更新角色 ──────────────────────────────────────────────────
    -- 超级管理员
    INSERT INTO roles (id, tenant_id, name, code, description, is_system)
    VALUES (
        gen_random_uuid(), v_tenant_id, '超级管理员', 'super_admin',
        '平台最高权限，管理所有资源、角色和系统配置', true
    )
    ON CONFLICT (tenant_id, name) DO UPDATE
    SET code = 'super_admin', description = EXCLUDED.description, is_system = true;

    -- 平台运营员
    INSERT INTO roles (id, tenant_id, name, code, description, is_system)
    VALUES (
        gen_random_uuid(), v_tenant_id, '平台运营员', 'platform_ops',
        '管理租户、模型、成本分析、用户和审计日志', true
    )
    ON CONFLICT (tenant_id, name) DO UPDATE
    SET code = 'platform_ops', description = EXCLUDED.description, is_system = true;

    -- 租户管理员
    INSERT INTO roles (id, tenant_id, name, code, description, is_system)
    VALUES (
        gen_random_uuid(), v_tenant_id, '租户管理员', 'tenant_admin',
        '租户内最高权限，管理成员和全部AI能力', true
    )
    ON CONFLICT (tenant_id, name) DO UPDATE
    SET code = 'tenant_admin', description = EXCLUDED.description, is_system = true;

    -- 租户开发者
    INSERT INTO roles (id, tenant_id, name, code, description, is_system)
    VALUES (
        gen_random_uuid(), v_tenant_id, '租户开发者', 'tenant_developer',
        'AI能力调用、Prompt/知识库/Agent/Workflow管理', true
    )
    ON CONFLICT (tenant_id, name) DO UPDATE
    SET code = 'tenant_developer', description = EXCLUDED.description, is_system = true;

    -- 租户观察者
    INSERT INTO roles (id, tenant_id, name, code, description, is_system)
    VALUES (
        gen_random_uuid(), v_tenant_id, '租户观察者', 'tenant_viewer',
        '只读查看对话记录、用量和配置', true
    )
    ON CONFLICT (tenant_id, name) DO UPDATE
    SET code = 'tenant_viewer', description = EXCLUDED.description, is_system = true;

    -- 获取角色 ID
    SELECT id INTO v_super_admin_id FROM roles WHERE code = 'super_admin' AND tenant_id = v_tenant_id;
    SELECT id INTO v_platform_ops_id FROM roles WHERE code = 'platform_ops' AND tenant_id = v_tenant_id;
    SELECT id INTO v_tenant_admin_id FROM roles WHERE code = 'tenant_admin' AND tenant_id = v_tenant_id;
    SELECT id INTO v_tenant_dev_id FROM roles WHERE code = 'tenant_developer' AND tenant_id = v_tenant_id;
    SELECT id INTO v_tenant_viewer_id FROM roles WHERE code = 'tenant_viewer' AND tenant_id = v_tenant_id;

    RAISE NOTICE '角色 ID: super_admin=%, platform_ops=%, tenant_admin=%, tenant_dev=%, tenant_viewer=%',
        v_super_admin_id, v_platform_ops_id, v_tenant_admin_id, v_tenant_dev_id, v_tenant_viewer_id;

    -- ── 删除旧的角色权限关联（清理旧数据）────────────────────────────────
    DELETE FROM role_permissions WHERE role_id IN (
        v_super_admin_id, v_platform_ops_id, v_tenant_admin_id,
        v_tenant_dev_id, v_tenant_viewer_id
    );
    RAISE NOTICE '已清理旧的 role_permissions 关联';

    -- ── 同时清理旧的角色（如果存在旧名称的角色，标记为非系统）────────────
    UPDATE roles SET is_system = false
    WHERE name IN ('管理员') AND code IS NULL AND tenant_id = v_tenant_id;

    -- ────────────────────────────────────────────────────────────────────────
    -- 2. 权限矩阵 — super_admin (全部 75 个权限)
    -- ────────────────────────────────────────────────────────────────────────
    FOR v_perm_id IN
        SELECT id FROM permissions
    LOOP
        INSERT INTO role_permissions (role_id, permission_id)
        VALUES (v_super_admin_id, v_perm_id)
        ON CONFLICT DO NOTHING;
    END LOOP;
    RAISE NOTICE 'super_admin: 已分配全部 % 个权限', (SELECT count(*) FROM permissions);

    -- ────────────────────────────────────────────────────────────────────────
    -- 3. 权限矩阵 — platform_ops (~35 个权限)
    --    平台管理类 CRUD + 所有资源只读
    -- ────────────────────────────────────────────────────────────────────────

    -- 3a. 平台管理资源: tenant, model_provider, cost, evaluation, user — 完整 CRUD
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_platform_ops_id, p.id
    FROM permissions p
    WHERE p.resource IN ('tenant', 'model_provider', 'cost', 'evaluation', 'user')
    ON CONFLICT DO NOTHING;

    -- 3b. 所有资源的 read 权限（只读）
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_platform_ops_id, p.id
    FROM permissions p
    WHERE p.action = 'read'
    ON CONFLICT DO NOTHING;

    -- 3c. audit_log 只读
    -- (已包含在 3b 中)

    RAISE NOTICE 'platform_ops: 已分配 % 个权限',
        (SELECT count(*) FROM role_permissions WHERE role_id = v_platform_ops_id);

    -- ────────────────────────────────────────────────────────────────────────
    -- 4. 权限矩阵 — tenant_admin (~50 个权限)
    --    租户内全AI能力 + 用户管理
    -- ────────────────────────────────────────────────────────────────────────

    -- 4a. AI 能力资源完整 CRUD: chat, conversation, knowledge_base, document,
    --     agent, tool, workflow, prompt — 全部操作
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_admin_id, p.id
    FROM permissions p
    WHERE p.resource IN (
        'chat', 'conversation', 'knowledge_base', 'document',
        'agent', 'tool', 'workflow', 'prompt'
    )
    ON CONFLICT DO NOTHING;

    -- 4b. model_provider 只读 + execute
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_admin_id, p.id
    FROM permissions p
    WHERE p.resource = 'model_provider' AND p.action IN ('read', 'execute')
    ON CONFLICT DO NOTHING;

    -- 4c. evaluation 完整 CRUD + execute
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_admin_id, p.id
    FROM permissions p
    WHERE p.resource = 'evaluation'
    ON CONFLICT DO NOTHING;

    -- 4d. cost 只读
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_admin_id, p.id
    FROM permissions p
    WHERE p.resource = 'cost' AND p.action = 'read'
    ON CONFLICT DO NOTHING;

    -- 4e. user: create + read + update
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_admin_id, p.id
    FROM permissions p
    WHERE p.resource = 'user' AND p.action IN ('create', 'read', 'update')
    ON CONFLICT DO NOTHING;

    -- 4f. role, tenant, audit_log 只读
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_admin_id, p.id
    FROM permissions p
    WHERE p.resource IN ('role', 'tenant', 'audit_log') AND p.action = 'read'
    ON CONFLICT DO NOTHING;

    RAISE NOTICE 'tenant_admin: 已分配 % 个权限',
        (SELECT count(*) FROM role_permissions WHERE role_id = v_tenant_admin_id);

    -- ────────────────────────────────────────────────────────────────────────
    -- 5. 权限矩阵 — tenant_developer (~40 个权限)
    --    AI能力全操作 + 管理资源只读
    -- ────────────────────────────────────────────────────────────────────────

    -- 5a. AI 能力资源: chat, conversation, knowledge_base, document,
    --     agent, tool, workflow, prompt — create/read/update/execute (no delete)
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_dev_id, p.id
    FROM permissions p
    WHERE p.resource IN (
        'chat', 'conversation', 'knowledge_base', 'agent',
        'tool', 'workflow', 'prompt'
    )
    AND p.action IN ('create', 'read', 'update', 'execute')
    ON CONFLICT DO NOTHING;

    -- 5b. document: create/read/update (no delete, no execute)
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_dev_id, p.id
    FROM permissions p
    WHERE p.resource = 'document' AND p.action IN ('create', 'read', 'update')
    ON CONFLICT DO NOTHING;

    -- 5c. model_provider: read + execute
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_dev_id, p.id
    FROM permissions p
    WHERE p.resource = 'model_provider' AND p.action IN ('read', 'execute')
    ON CONFLICT DO NOTHING;

    -- 5d. evaluation: create/read/update/execute (no delete)
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_dev_id, p.id
    FROM permissions p
    WHERE p.resource = 'evaluation' AND p.action IN ('create', 'read', 'update', 'execute')
    ON CONFLICT DO NOTHING;

    -- 5e. cost, user, role, tenant, audit_log 只读
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_dev_id, p.id
    FROM permissions p
    WHERE p.resource IN ('cost', 'user', 'role', 'tenant', 'audit_log')
    AND p.action = 'read'
    ON CONFLICT DO NOTHING;

    RAISE NOTICE 'tenant_developer: 已分配 % 个权限',
        (SELECT count(*) FROM role_permissions WHERE role_id = v_tenant_dev_id);

    -- ────────────────────────────────────────────────────────────────────────
    -- 6. 权限矩阵 — tenant_viewer (~15 个权限)
    --    所有资源只读
    -- ────────────────────────────────────────────────────────────────────────
    INSERT INTO role_permissions (role_id, permission_id)
    SELECT v_tenant_viewer_id, p.id
    FROM permissions p
    WHERE p.action = 'read'
    ON CONFLICT DO NOTHING;

    RAISE NOTICE 'tenant_viewer: 已分配 % 个权限',
        (SELECT count(*) FROM role_permissions WHERE role_id = v_tenant_viewer_id);

    -- ────────────────────────────────────────────────────────────────────────
    -- 7. 清理旧角色（移除不再使用的 "管理员" 角色，如有）
    -- ────────────────────────────────────────────────────────────────────────
    -- 注意：不自动删除旧角色，仅标记。如需清理请手动执行：
    -- DELETE FROM roles WHERE name = '管理员' AND code IS NULL;

END $$;

-- ────────────────────────────────────────────────────────────────────────────
-- 8. 验证 — 输出统计信息
-- ────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE
    v_total_roles int;
    v_total_perms int;
    v_total_assoc int;
BEGIN
    SELECT count(*) INTO v_total_roles FROM roles WHERE is_system = true;
    SELECT count(*) INTO v_total_perms FROM permissions;
    SELECT count(*) INTO v_total_assoc FROM role_permissions;

    RAISE NOTICE '════════════════════════════════════════════';
    RAISE NOTICE 'RBAC 初始化完成';
    RAISE NOTICE '════════════════════════════════════════════';
    RAISE NOTICE '系统角色数: %', v_total_roles;
    RAISE NOTICE '权限总数:   %', v_total_perms;
    RAISE NOTICE '角色权限关联: %', v_total_assoc;
    RAISE NOTICE '════════════════════════════════════════════';
END $$;

-- ────────────────────────────────────────────────────────────────────────────
-- 9. 查询视图 — 方便查看分配结果
-- ────────────────────────────────────────────────────────────────────────────

-- 查看各角色的权限数量
SELECT r.name AS "角色", r.code AS "角色代码", count(rp.permission_id) AS "权限数"
FROM roles r
LEFT JOIN role_permissions rp ON r.id = rp.role_id
WHERE r.is_system = true
GROUP BY r.name, r.code
ORDER BY "权限数" DESC;

-- 查看权限矩阵详情（按角色代码分组）
SELECT
    p.resource || '.' || p.action AS "权限",
    bool_or(r.code = 'super_admin') AS "super_admin",
    bool_or(r.code = 'platform_ops') AS "platform_ops",
    bool_or(r.code = 'tenant_admin') AS "tenant_admin",
    bool_or(r.code = 'tenant_developer') AS "tenant_dev",
    bool_or(r.code = 'tenant_viewer') AS "tenant_viewer"
FROM permissions p
LEFT JOIN role_permissions rp ON p.id = rp.permission_id
LEFT JOIN roles r ON rp.role_id = r.id AND r.is_system = true
GROUP BY p.resource, p.action
ORDER BY p.resource,
    CASE p.action
        WHEN 'create' THEN 1
        WHEN 'read' THEN 2
        WHEN 'update' THEN 3
        WHEN 'delete' THEN 4
        WHEN 'execute' THEN 5
    END;

COMMIT;
