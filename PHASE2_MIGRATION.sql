-- ╔════════════════════════════════════════════════════════════════════════════╗
-- ║            PHASE 2: COMPLETE DATABASE MIGRATION - PRODUCTION READY          ║
-- ║         Run this SQL in Supabase SQL Editor to set up Phase 2              ║
-- ╚════════════════════════════════════════════════════════════════════════════╝

-- ════════════════════════════════════════════════════════════════════════════
-- 1. CREATE RESOURCES TABLE (stores template + knowledge documents)
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS resources (
    id BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    resource_type TEXT NOT NULL,  -- 'template', 'knowledge', 'reference'
    is_template BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    content TEXT,  -- Full document content
    storage_path TEXT,  -- Path in Supabase Storage (if stored separately)
    metadata JSONB DEFAULT '{}',  -- {author, tags, version, etc}
    embedding vector(768),  -- Gemini text-embedding-004 (768-dim)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by UUID,
    updated_by UUID
);

-- Create indexes for fast queries
CREATE INDEX idx_resources_workspace_id ON resources(workspace_id);
CREATE INDEX idx_resources_is_active ON resources(is_active);
CREATE INDEX idx_resources_is_template ON resources(is_template);
CREATE INDEX idx_resources_resource_type ON resources(resource_type);
CREATE INDEX idx_resources_embedding ON resources USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Enable RLS (Row Level Security)
ALTER TABLE resources ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see their workspace resources
CREATE POLICY "Users can read their workspace resources"
    ON resources
    FOR SELECT
    USING (workspace_id = current_user_id() OR current_user_id() IS NULL);

-- ════════════════════════════════════════════════════════════════════════════
-- 2. CREATE RESOURCE_CHUNKS TABLE (for paragraph-level retrieval)
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS resource_chunks (
    id BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL,
    resource_id BIGINT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    chunk_order INT,  -- Position in document (0, 1, 2...)
    chunk_content TEXT NOT NULL,  -- Paragraph/section text
    chunk_size INT,  -- Character count
    metadata JSONB DEFAULT '{}',  -- {page_number, section_title, etc}
    embedding vector(768),  -- Gemini text-embedding-004 (768-dim)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for fast queries
CREATE INDEX idx_resource_chunks_workspace_id ON resource_chunks(workspace_id);
CREATE INDEX idx_resource_chunks_resource_id ON resource_chunks(resource_id);
CREATE INDEX idx_resource_chunks_embedding ON resource_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Enable RLS
ALTER TABLE resource_chunks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read chunks from their workspace resources"
    ON resource_chunks
    FOR SELECT
    USING (
        workspace_id = current_user_id() OR current_user_id() IS NULL OR
        resource_id IN (SELECT id FROM resources WHERE workspace_id = current_user_id())
    );

-- ════════════════════════════════════════════════════════════════════════════
-- 3. CREATE AUDIT TABLES (logging policy decisions + resource access)
-- ════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS policy_decisions (
    id BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL,
    atom_id TEXT NOT NULL,
    policy_id TEXT,
    decision TEXT NOT NULL,  -- 'GREEN', 'YELLOW', 'RED'
    confidence FLOAT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_policy_decisions_workspace_id ON policy_decisions(workspace_id);
CREATE INDEX idx_policy_decisions_atom_id ON policy_decisions(atom_id);
CREATE INDEX idx_policy_decisions_decision ON policy_decisions(decision);

ALTER TABLE policy_decisions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read policy decisions from their workspace"
    ON policy_decisions
    FOR SELECT
    USING (workspace_id = current_user_id() OR current_user_id() IS NULL);

-- Resource Access Logs
CREATE TABLE IF NOT EXISTS resource_access_logs (
    id BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL,
    atom_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_type TEXT,  -- 'template', 'knowledge', 'chunk'
    retrieved BOOLEAN DEFAULT TRUE,
    source TEXT,  -- Document name or chunk source
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_resource_access_logs_workspace_id ON resource_access_logs(workspace_id);
CREATE INDEX idx_resource_access_logs_atom_id ON resource_access_logs(atom_id);
CREATE INDEX idx_resource_access_logs_resource_id ON resource_access_logs(resource_id);

ALTER TABLE resource_access_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read resource access logs from their workspace"
    ON resource_access_logs
    FOR SELECT
    USING (workspace_id = current_user_id() OR current_user_id() IS NULL);

-- ════════════════════════════════════════════════════════════════════════════
-- 4. CREATE RPC FUNCTION: match_policies()
-- ════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION match_policies(
    query_embedding vector,
    workspace_id UUID,
    similarity_threshold FLOAT DEFAULT 0.7,
    match_limit INT DEFAULT 5
)
RETURNS TABLE (
    id BIGINT,
    type TEXT,
    rule_text TEXT,
    similarity FLOAT,
    workspace_id UUID
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        pr.id,
        pr.type,
        pr.rule_text,
        (1 - (pr.embedding <=> query_embedding))::FLOAT AS similarity,
        pr.workspace_id
    FROM policy_rules pr
    WHERE pr.workspace_id = match_policies.workspace_id
    AND pr.is_active = TRUE
    AND (1 - (pr.embedding <=> query_embedding)) > similarity_threshold
    ORDER BY pr.embedding <=> query_embedding
    LIMIT match_limit;
END;
$$ LANGUAGE plpgsql;

-- ════════════════════════════════════════════════════════════════════════════
-- 5. CREATE RPC FUNCTION: match_resources()
-- ════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION match_resources(
    query_embedding vector,
    workspace_id UUID,
    similarity_threshold FLOAT DEFAULT 0.75,
    match_limit INT DEFAULT 5,
    is_template BOOLEAN DEFAULT NULL
)
RETURNS TABLE (
    id BIGINT,
    title TEXT,
    resource_type TEXT,
    description TEXT,
    similarity FLOAT,
    storage_path TEXT,
    workspace_id UUID
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        r.id,
        r.title,
        r.resource_type,
        r.description,
        (1 - (r.embedding <=> query_embedding))::FLOAT AS similarity,
        r.storage_path,
        r.workspace_id
    FROM resources r
    WHERE r.workspace_id = match_resources.workspace_id
    AND r.is_active = TRUE
    AND (is_template IS NULL OR r.is_template = is_template)
    AND r.embedding IS NOT NULL
    AND (1 - (r.embedding <=> query_embedding)) > similarity_threshold
    ORDER BY r.embedding <=> query_embedding
    LIMIT match_limit;
END;
$$ LANGUAGE plpgsql;

-- ════════════════════════════════════════════════════════════════════════════
-- 6. CREATE RPC FUNCTION: match_resource_chunks()
-- ════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION match_resource_chunks(
    query_embedding vector,
    workspace_id UUID,
    similarity_threshold FLOAT DEFAULT 0.75,
    match_limit INT DEFAULT 5
)
RETURNS TABLE (
    id BIGINT,
    resource_id BIGINT,
    chunk_content TEXT,
    chunk_order INT,
    similarity FLOAT,
    source_document TEXT,
    workspace_id UUID
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        rc.id,
        rc.resource_id,
        rc.chunk_content,
        rc.chunk_order,
        (1 - (rc.embedding <=> query_embedding))::FLOAT AS similarity,
        r.title AS source_document,
        rc.workspace_id
    FROM resource_chunks rc
    JOIN resources r ON rc.resource_id = r.id
    WHERE rc.workspace_id = match_resource_chunks.workspace_id
    AND r.is_active = TRUE
    AND rc.embedding IS NOT NULL
    AND (1 - (rc.embedding <=> query_embedding)) > similarity_threshold
    ORDER BY rc.embedding <=> query_embedding
    LIMIT match_limit;
END;
$$ LANGUAGE plpgsql;

-- ════════════════════════════════════════════════════════════════════════════
-- 7. GRANT EXECUTE PERMISSIONS ON RPC FUNCTIONS
-- ════════════════════════════════════════════════════════════════════════════

GRANT EXECUTE ON FUNCTION match_policies(vector, UUID, FLOAT, INT) TO authenticated;
GRANT EXECUTE ON FUNCTION match_resources(vector, UUID, FLOAT, INT, BOOLEAN) TO authenticated;
GRANT EXECUTE ON FUNCTION match_resource_chunks(vector, UUID, FLOAT, INT) TO authenticated;

-- ════════════════════════════════════════════════════════════════════════════
-- 8. SAMPLE DATA (optional - for testing)
-- ════════════════════════════════════════════════════════════════════════════

-- Insert sample resource (requires a workspace_id UUID)
-- UPDATE THIS WITH YOUR ACTUAL WORKSPACE ID
INSERT INTO resources (
    workspace_id,
    title,
    description,
    resource_type,
    is_template,
    content,
    embedding
)
VALUES (
    '00000000-0000-0000-0000-000000000000'::UUID,  -- REPLACE WITH YOUR WORKSPACE ID
    'Sample Offer Letter Template',
    'Standard offer letter template for new hires',
    'template',
    TRUE,
    'Dear [CANDIDATE_NAME], We are pleased to offer you...',
    '[0.1, 0.2, 0.3, ...]'::vector  -- REPLACE WITH REAL EMBEDDING
)
ON CONFLICT DO NOTHING;

-- ════════════════════════════════════════════════════════════════════════════
-- 9. MIGRATION COMPLETE - VERIFY SETUP
-- ════════════════════════════════════════════════════════════════════════════

-- Run these queries to verify:
-- SELECT COUNT(*) FROM resources;
-- SELECT COUNT(*) FROM resource_chunks;
-- SELECT * FROM policy_rules LIMIT 1;  -- Should already exist from Phase 1
-- \dx+ match_policies  -- Verify RPC exists

-- ════════════════════════════════════════════════════════════════════════════
-- SUCCESS: Phase 2 database infrastructure is ready!
-- Next: Generate embeddings for your resources using Gemini API
-- ════════════════════════════════════════════════════════════════════════════
