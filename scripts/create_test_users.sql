-- ============================================================================
-- 创建 5 个测试用户（每个角色一个），用于前端权限验证
-- 密码统一: test123 (bcrypt hash)
-- 执行方式: psql -h <host> -U <user> -d <database> -f create_test_users.sql
-- ============================================================================

BEGIN;

-- 获取默认租户
DO $$
DECLARE
    v_tenant_id UUID;
    v_role_super_admin UUID;
    v_role_platform_ops UUID;
    v_role_tenant_admin UUID;
    v_role_tenant_dev UUID;
    v_role_tenant_viewer UUID;
    v_user_id UUID;
    v_password_hash TEXT := '$2b$12$ux/dpV18vldsKi0Ofmi67.mFI.cDe8gbvy4c3zrzlyWVaOuMEJS7C';
BEGIN
    -- 获取默认租户
    SELECT id INTO v_tenant_id FROM tenants WHERE slug = 'default' LIMIT 1;
    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION '默认租户不存在';
    END IF;

    -- 获取角色 ID
    SELECT id INTO v_role_super_admin FROM roles WHERE code = 'super_admin' AND tenant_id = v_tenant_id;
    SELECT id INTO v_role_platform_ops FROM roles WHERE code = 'platform_ops' AND tenant_id = v_tenant_id;
    SELECT id INTO v_role_tenant_admin FROM roles WHERE code = 'tenant_admin' AND tenant_id = v_tenant_id;
    SELECT id INTO v_role_tenant_dev FROM roles WHERE code = 'tenant_developer' AND tenant_id = v_tenant_id;
    SELECT id INTO v_role_tenant_viewer FROM roles WHERE code = 'tenant_viewer' AND tenant_id = v_tenant_id;

    RAISE NOTICE '租户: %', v_tenant_id;
    RAISE NOTICE '角色: super_admin=%, platform_ops=%, tenant_admin=%, tenant_dev=%, tenant_viewer=%',
        v_role_super_admin, v_role_platform_ops, v_role_tenant_admin, v_role_tenant_dev, v_role_tenant_viewer;

    -- ── 1. 超级管理员 ─────────────────────────────────────────────────────
    IF NOT EXISTS (SELECT 1 FROM users WHERE username = 'test_sa' AND tenant_id = v_tenant_id) THEN
        INSERT INTO users (id, tenant_id, username, email, password_hash, display_name, is_active, is_superadmin)
        VALUES (gen_random_uuid(), v_tenant_id, 'test_sa', 'test_sa@ai-platform.local',
                v_password_hash, '测试-超级管理员', true, true);
    END IF;
    SELECT id INTO v_user_id FROM users WHERE username = 'test_sa' AND tenant_id = v_tenant_id;
    INSERT INTO user_roles (user_id, role_id) VALUES (v_user_id, v_role_super_admin)
    ON CONFLICT DO NOTHING;
    RAISE NOTICE '用户 test_sa / test123 → super_admin (id=%)', v_user_id;

    -- ── 2. 平台运营员 ─────────────────────────────────────────────────────
    IF NOT EXISTS (SELECT 1 FROM users WHERE username = 'test_ops' AND tenant_id = v_tenant_id) THEN
        INSERT INTO users (id, tenant_id, username, email, password_hash, display_name, is_active, is_superadmin)
        VALUES (gen_random_uuid(), v_tenant_id, 'test_ops', 'test_ops@ai-platform.local',
                v_password_hash, '测试-平台运营', true, false);
    END IF;
    SELECT id INTO v_user_id FROM users WHERE username = 'test_ops' AND tenant_id = v_tenant_id;
    INSERT INTO user_roles (user_id, role_id) VALUES (v_user_id, v_role_platform_ops)
    ON CONFLICT DO NOTHING;
    RAISE NOTICE '用户 test_ops / test123 → platform_ops (id=%)', v_user_id;

    -- ── 3. 租户管理员 ─────────────────────────────────────────────────────
    IF NOT EXISTS (SELECT 1 FROM users WHERE username = 'test_ta' AND tenant_id = v_tenant_id) THEN
        INSERT INTO users (id, tenant_id, username, email, password_hash, display_name, is_active, is_superadmin)
        VALUES (gen_random_uuid(), v_tenant_id, 'test_ta', 'test_ta@ai-platform.local',
                v_password_hash, '测试-租户管理员', true, false);
    END IF;
    SELECT id INTO v_user_id FROM users WHERE username = 'test_ta' AND tenant_id = v_tenant_id;
    INSERT INTO user_roles (user_id, role_id) VALUES (v_user_id, v_role_tenant_admin)
    ON CONFLICT DO NOTHING;
    RAISE NOTICE '用户 test_ta / test123 → tenant_admin (id=%)', v_user_id;

    -- ── 4. 租户开发者 ─────────────────────────────────────────────────────
    IF NOT EXISTS (SELECT 1 FROM users WHERE username = 'test_dev' AND tenant_id = v_tenant_id) THEN
        INSERT INTO users (id, tenant_id, username, email, password_hash, display_name, is_active, is_superadmin)
        VALUES (gen_random_uuid(), v_tenant_id, 'test_dev', 'test_dev@ai-platform.local',
                v_password_hash, '测试-开发者', true, false);
    END IF;
    SELECT id INTO v_user_id FROM users WHERE username = 'test_dev' AND tenant_id = v_tenant_id;
    INSERT INTO user_roles (user_id, role_id) VALUES (v_user_id, v_role_tenant_dev)
    ON CONFLICT DO NOTHING;
    RAISE NOTICE '用户 test_dev / test123 → tenant_developer (id=%)', v_user_id;

    -- ── 5. 租户观察者 ─────────────────────────────────────────────────────
    IF NOT EXISTS (SELECT 1 FROM users WHERE username = 'test_viewer' AND tenant_id = v_tenant_id) THEN
        INSERT INTO users (id, tenant_id, username, email, password_hash, display_name, is_active, is_superadmin)
        VALUES (gen_random_uuid(), v_tenant_id, 'test_viewer', 'test_viewer@ai-platform.local',
                v_password_hash, '测试-观察者', true, false);
    END IF;
    SELECT id INTO v_user_id FROM users WHERE username = 'test_viewer' AND tenant_id = v_tenant_id;
    INSERT INTO user_roles (user_id, role_id) VALUES (v_user_id, v_role_tenant_viewer)
    ON CONFLICT DO NOTHING;
    RAISE NOTICE '用户 test_viewer / test123 → tenant_viewer (id=%)', v_user_id;

END $$;

-- 验证
SELECT u.username, u.display_name, r.name AS role_name, r.code AS role_code
FROM users u
JOIN user_roles ur ON ur.user_id = u.id
JOIN roles r ON r.id = ur.role_id
WHERE u.username LIKE 'test_%'
ORDER BY r.code;

COMMIT;
