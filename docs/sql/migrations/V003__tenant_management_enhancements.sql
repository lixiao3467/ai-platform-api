-- ============================================================================
-- V003: Tenant Management Enhancements
-- Description: Add tenant self-service, model access control, feature flags,
--              quota tracking, and enhanced RBAC
-- Date: 2026-08-05
-- ============================================================================

BEGIN;

-- ============================================================================
-- 1. New Tables
-- ============================================================================

-- 1.1 Tenant model access control
CREATE TABLE IF NOT EXISTS tenant_model_access (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    model_id VARCHAR(64) NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT true,
    rate_limit INTEGER,
    quota_limit INTEGER,
    quota_used INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, model_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_model_access_tenant ON tenant_model_access(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_model_access_model ON tenant_model_access(model_id);

-- 1.2 API Key permissions (structured, relational — replaces JSON list)
CREATE TABLE IF NOT EXISTS api_key_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    resource_id UUID,  -- Optional: scope to specific resource
    allowed_models JSONB NOT NULL DEFAULT '[]',
    rate_limit_override INTEGER,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(api_key_id, permission_id, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_api_key_permissions_key ON api_key_permissions(api_key_id);
CREATE INDEX IF NOT EXISTS idx_api_key_permissions_permission ON api_key_permissions(permission_id);

-- 1.3 Tenant feature flags
CREATE TABLE IF NOT EXISTS tenant_feature_flags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    feature_name VARCHAR(64) NOT NULL,
    is_enabled BOOLEAN NOT NULL DEFAULT false,
    config JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, feature_name)
);

CREATE INDEX IF NOT EXISTS idx_tenant_feature_flags_tenant ON tenant_feature_flags(tenant_id);

-- 1.4 Quota usage tracking
CREATE TABLE IF NOT EXISTS quota_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    resource_type VARCHAR(32) NOT NULL,  -- model_calls, storage, users, etc.
    resource_id VARCHAR(128),
    usage_count BIGINT NOT NULL DEFAULT 0,
    usage_amount BIGINT NOT NULL DEFAULT 0,  -- tokens, bytes, etc.
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, resource_type, resource_id, period_start)
);

CREATE INDEX IF NOT EXISTS idx_quota_usage_tenant ON quota_usage(tenant_id);
CREATE INDEX IF NOT EXISTS idx_quota_usage_period ON quota_usage(period_start, period_end);

-- ============================================================================
-- 2. Enhance Existing Tables
-- ============================================================================

-- 2.1 Enhance tenants table
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS admin_email VARCHAR(256);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS admin_user_id UUID REFERENCES users(id);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS max_users INTEGER NOT NULL DEFAULT 10;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS max_apps INTEGER NOT NULL DEFAULT 5;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS max_api_keys_per_app INTEGER NOT NULL DEFAULT 10;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS allowed_features JSONB NOT NULL DEFAULT '[]';
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS custom_domain VARCHAR(256);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS branding_config JSONB NOT NULL DEFAULT '{}';

-- 2.2 Enhance api_keys table
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS tenant_id UUID REFERENCES tenants(id);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used_ip VARCHAR(45);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS total_requests BIGINT NOT NULL DEFAULT 0;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS total_tokens BIGINT NOT NULL DEFAULT 0;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS allowed_models JSONB NOT NULL DEFAULT '[]';
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS ip_whitelist JSONB NOT NULL DEFAULT '[]';
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS scope VARCHAR(16) NOT NULL DEFAULT 'app';

-- 2.3 Populate tenant_id for existing API keys (via app relationship)
UPDATE api_keys SET tenant_id = apps.tenant_id FROM apps WHERE api_keys.app_id = apps.id AND api_keys.tenant_id IS NULL;

-- 2.4 Set tenant_id NOT NULL after backfill
ALTER TABLE api_keys ALTER COLUMN tenant_id SET NOT NULL;

-- 2.5 Enhance permissions table with scope
ALTER TABLE permissions ADD COLUMN IF NOT EXISTS scope VARCHAR(32) NOT NULL DEFAULT 'tenant';
ALTER TABLE permissions ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT false;

-- 2.6 Enhance roles table with scope
ALTER TABLE roles ADD COLUMN IF NOT EXISTS scope VARCHAR(32) NOT NULL DEFAULT 'tenant';

-- ============================================================================
-- 3. Initialize Default Permissions & Roles
-- ============================================================================

-- 3.1 Platform-level permissions (skip if already exist)
INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'platform', 'tenant', 'manage', 'Manage all tenants', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'tenant' AND action = 'manage' AND scope = 'platform');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'platform', 'tenant', 'view_all', 'View all tenants', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'tenant' AND action = 'view_all' AND scope = 'platform');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'platform', 'model', 'manage', 'Manage model configuration', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'model' AND action = 'manage' AND scope = 'platform');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'platform', 'system', 'config', 'System configuration', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'system' AND action = 'config' AND scope = 'platform');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'platform', 'audit', 'view_all', 'View all audit logs', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'audit' AND action = 'view_all' AND scope = 'platform');

-- 3.2 Tenant-level permissions
INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'tenant', 'config', 'Configure tenant settings', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'tenant' AND action = 'config' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'tenant', 'quota_view', 'View quota usage', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'tenant' AND action = 'quota_view' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'apikey', 'manage', 'Manage API keys', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'apikey' AND action = 'manage' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'user', 'manage', 'Manage tenant users', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'user' AND action = 'manage' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'app', 'manage', 'Manage apps', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'app' AND action = 'manage' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'agent', 'manage', 'Manage agents', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'agent' AND action = 'manage' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'workflow', 'manage', 'Manage workflows', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'workflow' AND action = 'manage' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'knowledge', 'manage', 'Manage knowledge bases', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'knowledge' AND action = 'manage' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'prompt', 'manage', 'Manage prompts', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'prompt' AND action = 'manage' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'tool', 'manage', 'Manage tools', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'tool' AND action = 'manage' AND scope = 'tenant');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'tenant', 'audit', 'view', 'View tenant audit logs', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'audit' AND action = 'view' AND scope = 'tenant');

-- 3.3 App-level permissions
INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'app', 'chat', 'use', 'Use chat functionality', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'chat' AND action = 'use' AND scope = 'app');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'app', 'agent', 'execute', 'Execute agents', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'agent' AND action = 'execute' AND scope = 'app');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'app', 'workflow', 'execute', 'Execute workflows', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'workflow' AND action = 'execute' AND scope = 'app');

INSERT INTO permissions (id, scope, resource, action, description, is_system)
SELECT gen_random_uuid(), 'app', 'knowledge', 'query', 'Query knowledge bases', true
WHERE NOT EXISTS (SELECT 1 FROM permissions WHERE resource = 'knowledge' AND action = 'query' AND scope = 'app');

-- 3.4 Default roles (tenant_id = NULL means system-level role)
INSERT INTO roles (id, tenant_id, scope, name, description, is_system)
SELECT gen_random_uuid(), NULL, 'platform', 'Platform Admin', 'Platform administrator with full access', true
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'Platform Admin' AND tenant_id IS NULL);

INSERT INTO roles (id, tenant_id, scope, name, description, is_system)
SELECT gen_random_uuid(), NULL, 'tenant', 'Tenant Admin', 'Tenant administrator with full tenant access', true
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'Tenant Admin' AND tenant_id IS NULL);

INSERT INTO roles (id, tenant_id, scope, name, description, is_system)
SELECT gen_random_uuid(), NULL, 'tenant', 'Tenant User', 'Tenant user with limited access', true
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'Tenant User' AND tenant_id IS NULL);

-- 3.5 Initialize default feature flags for all existing tenants
INSERT INTO tenant_feature_flags (tenant_id, feature_name, is_enabled, config)
SELECT
    t.id,
    f.feature_name,
    true,  -- Enable all features for existing tenants by default
    '{}'
FROM tenants t
CROSS JOIN (
    VALUES ('rag'), ('agent'), ('workflow'), ('prompt_management'), ('tool_management')
) AS f(feature_name)
ON CONFLICT (tenant_id, feature_name) DO NOTHING;

-- 3.6 Record migration version
INSERT INTO schema_versions (version, description, applied_at, applied_by, checksum)
VALUES (
    'V003',
    'Tenant management enhancements: model access control, feature flags, quota tracking, enhanced RBAC',
    NOW(),
    current_user,
    md5('V003__tenant_management_enhancements')
)
ON CONFLICT DO NOTHING;

COMMIT;
