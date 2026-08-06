-- ============================================================================
-- V005: Knowledge Base Redesign
-- Description: Add knowledge_groups table, group_id to KB, document tracking fields
-- Date: 2026-08-06
-- ============================================================================

BEGIN;

-- 1. Create knowledge_groups table
CREATE TABLE IF NOT EXISTS knowledge_groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES knowledge_groups(id) ON DELETE SET NULL,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    icon VARCHAR(64),
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_kg_tenant_name UNIQUE(tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_kg_tenant ON knowledge_groups(tenant_id);
CREATE INDEX IF NOT EXISTS idx_kg_parent ON knowledge_groups(parent_id);

-- 2. Add group_id to knowledge_bases
ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS group_id UUID REFERENCES knowledge_groups(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_kb_group ON knowledge_bases(group_id);

-- 3. Add tracking fields to documents
ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_hash VARCHAR(64);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS parse_result_path TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_progress JSONB DEFAULT '{}';

-- 4. Indexes for document queries
CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash) WHERE file_hash IS NOT NULL;

COMMIT;
