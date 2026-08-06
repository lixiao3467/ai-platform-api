-- ============================================================================
-- V003 Rollback: Revert tenant management enhancements
-- ============================================================================

BEGIN;

-- Drop new tables
DROP TABLE IF EXISTS quota_usage;
DROP TABLE IF EXISTS tenant_feature_flags;
DROP TABLE IF EXISTS api_key_permissions;
DROP TABLE IF EXISTS tenant_model_access;

-- Revert tenant columns
ALTER TABLE tenants DROP COLUMN IF EXISTS branding_config;
ALTER TABLE tenants DROP COLUMN IF EXISTS custom_domain;
ALTER TABLE tenants DROP COLUMN IF EXISTS allowed_features;
ALTER TABLE tenants DROP COLUMN IF EXISTS max_api_keys_per_app;
ALTER TABLE tenants DROP COLUMN IF EXISTS max_apps;
ALTER TABLE tenants DROP COLUMN IF EXISTS max_users;
ALTER TABLE tenants DROP COLUMN IF EXISTS admin_user_id;
ALTER TABLE tenants DROP COLUMN IF EXISTS admin_email;

-- Revert api_keys columns
ALTER TABLE api_keys DROP COLUMN IF EXISTS scope;
ALTER TABLE api_keys DROP COLUMN IF EXISTS ip_whitelist;
ALTER TABLE api_keys DROP COLUMN IF EXISTS allowed_models;
ALTER TABLE api_keys DROP COLUMN IF EXISTS total_tokens;
ALTER TABLE api_keys DROP COLUMN IF EXISTS total_requests;
ALTER TABLE api_keys DROP COLUMN IF EXISTS last_used_ip;
ALTER TABLE api_keys DROP COLUMN IF EXISTS description;
ALTER TABLE api_keys DROP COLUMN IF EXISTS created_by;
ALTER TABLE api_keys DROP COLUMN IF EXISTS tenant_id;

-- Revert permissions/roles columns
ALTER TABLE permissions DROP COLUMN IF EXISTS is_system;
ALTER TABLE permissions DROP COLUMN IF EXISTS scope;
ALTER TABLE roles DROP COLUMN IF EXISTS scope;

-- Remove migration version record
DELETE FROM schema_versions WHERE version = 'V003';

COMMIT;
