-- V004: Security fixes + performance indexes
-- Addresses critical code review findings (auth.py + models.py)

-- =============================================================================
-- Performance indexes (missing FK indexes were causing seq scans)
-- =============================================================================

-- API key lookup hot path (verify_api_key does this on every request)
CREATE INDEX IF NOT EXISTS idx_api_key_hash_prefix ON api_keys (key_hash, key_prefix);

-- Tenant-scoped listing (admin dashboards, retention jobs)
CREATE INDEX IF NOT EXISTS idx_conv_tenant ON conversations (tenant_id);
CREATE INDEX IF NOT EXISTS idx_conv_app ON conversations (app_id);
CREATE INDEX IF NOT EXISTS idx_agent_tenant ON agents (tenant_id);
CREATE INDEX IF NOT EXISTS idx_agent_app ON agents (app_id);

-- Knowledge base chunk listing (re-indexing, cleanup)
CREATE INDEX IF NOT EXISTS idx_chunks_kb ON document_chunks (kb_id);

-- Workflow execution listing
CREATE INDEX IF NOT EXISTS idx_wf_exec_workflow ON workflow_executions (workflow_id);
CREATE INDEX IF NOT EXISTS idx_wf_exec_tenant ON workflow_executions (tenant_id);

-- Workflow step listing
CREATE INDEX IF NOT EXISTS idx_wf_step_execution ON workflow_steps (execution_id);

-- =============================================================================
-- Schema fixes
-- =============================================================================

-- Drop old case-sensitive email index (if exists)
DROP INDEX IF EXISTS idx_users_email;

-- Recreate as case-insensitive functional index
CREATE UNIQUE INDEX idx_users_email ON users (lower(email));

-- =============================================================================
-- Column size fixes (apply on next table rewrite or for new deployments)
-- NOTE: These ALTER statements are non-blocking in PostgreSQL but may take time
-- on large tables. For production, consider running during maintenance window.
-- =============================================================================

-- key_hash: SHA-256 hex = 64 chars; was 256 (over-allocated)
-- password_hash: accommodate argon2 (up to ~500 chars); was 256 (too small)
-- These are safe ALTERs — PostgreSQL allows increasing VARCHAR length without rewrite.

ALTER TABLE api_keys ALTER COLUMN key_hash TYPE VARCHAR(64);
ALTER TABLE users ALTER COLUMN password_hash TYPE VARCHAR(512);

-- =============================================================================
-- RBAC: Ensure role code uniqueness per tenant (was managed by SQL only)
-- =============================================================================

-- Add unique constraint for (tenant_id, code) on roles
-- This prevents duplicate role codes within a tenant
ALTER TABLE roles ADD CONSTRAINT uq_roles_tenant_code UNIQUE (tenant_id, code);
