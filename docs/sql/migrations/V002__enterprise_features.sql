-- V002: Enterprise features — SSO providers, API key enhancements, audit indexes
-- Date: 2026-08-05
-- Author: Backend Architect
--
-- Changes:
--   1. Add sso_providers table for SSO/SAML/OIDC integration
--   2. Add is_enabled column to api_keys for enable/disable toggle
--   3. Add additional indexes for audit_logs query performance
--   4. Add index for audit_logs action filtering

-- =============================================================================
-- 1. SSO Providers table
-- =============================================================================

CREATE TABLE IF NOT EXISTS sso_providers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider_type VARCHAR(32) NOT NULL,       -- oidc | oauth2 | saml | feishu | dingtalk | wecom
    name VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    client_id VARCHAR(256) NOT NULL,
    client_secret_encrypted VARCHAR(1024) NOT NULL,
    issuer_url VARCHAR(512),
    redirect_uri VARCHAR(512),
    scopes JSONB DEFAULT '[]',
    extra_config JSONB DEFAULT '{}',
    is_enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unique constraint: one provider per name per tenant
CREATE UNIQUE INDEX IF NOT EXISTS idx_sso_tenant_name
    ON sso_providers (tenant_id, name);

-- Tenant lookup
CREATE INDEX IF NOT EXISTS idx_sso_tenant
    ON sso_providers (tenant_id);


-- =============================================================================
-- 2. API Keys — add is_enabled column
-- =============================================================================

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN DEFAULT TRUE;


-- =============================================================================
-- 3. Audit logs — additional performance indexes
-- =============================================================================

-- Index for action filtering (used by audit log stats and filtering)
CREATE INDEX IF NOT EXISTS idx_audit_action
    ON audit_logs (action);

-- Index for resource type filtering
CREATE INDEX IF NOT EXISTS idx_audit_resource_type
    ON audit_logs (resource_type);

-- Index for user_id filtering (used when querying logs by user)
CREATE INDEX IF NOT EXISTS idx_audit_user_id
    ON audit_logs (user_id) WHERE user_id IS NOT NULL;

-- Index for response code range queries (used for error stats)
CREATE INDEX IF NOT EXISTS idx_audit_response_code
    ON audit_logs (response_code) WHERE response_code IS NOT NULL;

-- Index for api_key_prefix lookup
CREATE INDEX IF NOT EXISTS idx_audit_api_key_prefix
    ON audit_logs (api_key_prefix) WHERE api_key_prefix IS NOT NULL;


-- =============================================================================
-- 4. Update updated_at trigger for sso_providers
-- =============================================================================

-- Ensure updated_at is auto-maintained
CREATE OR REPLACE FUNCTION update_sso_providers_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_sso_providers_updated_at ON sso_providers;
CREATE TRIGGER trigger_sso_providers_updated_at
    BEFORE UPDATE ON sso_providers
    FOR EACH ROW
    EXECUTE FUNCTION update_sso_providers_updated_at();
