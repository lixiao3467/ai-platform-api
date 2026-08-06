-- =============================================================================
-- Schema Version Tracking Table
-- =============================================================================
-- This table tracks which SQL migrations have been applied to the database.
-- DBAs should insert a record after applying each migration script.
-- =============================================================================

CREATE TABLE IF NOT EXISTS schema_versions (
    version VARCHAR(50) PRIMARY KEY,
    description TEXT,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    applied_by VARCHAR(100) DEFAULT current_user,
    checksum VARCHAR(64),
    execution_time_ms INTEGER
);

-- Add comment for documentation
COMMENT ON TABLE schema_versions IS 'Tracks applied SQL migrations';
COMMENT ON COLUMN schema_versions.version IS 'Migration version (e.g., V001)';
COMMENT ON COLUMN schema_versions.description IS 'Migration description';
COMMENT ON COLUMN schema_versions.applied_at IS 'When the migration was applied';
COMMENT ON COLUMN schema_versions.applied_by IS 'Database user who applied it';
COMMENT ON COLUMN schema_versions.checksum IS 'SHA256 checksum of the SQL file';
COMMENT ON COLUMN schema_versions.execution_time_ms IS 'How long the migration took';

-- Example: Record initial schema
-- INSERT INTO schema_versions (version, description, checksum)
-- VALUES ('V001', 'Initial schema - all tables', 'abc123...');
