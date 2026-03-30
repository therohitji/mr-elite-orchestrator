-- ============================================================================
-- PHASE 2: MEMORY LAYER SCHEMA
-- ============================================================================
-- Tables: memory_items, memory_vectors, workflow_states
-- Purpose: Episodic + Reflective Memory + Workflow State Checkpointing
-- ============================================================================

-- Enable vector extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- TABLE: memory_items (Canonical Memory Records)
-- ============================================================================
-- Stores all memory types: state, episode, semantic, preference, entity_note
-- ============================================================================

CREATE TABLE IF NOT EXISTS memory_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  
  -- Multi-tenancy
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
  
  -- Ownership & audit
  created_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  
  -- Memory classification
  memory_type TEXT NOT NULL 
    CHECK (memory_type IN ('state', 'episode', 'semantic', 'preference', 'entity_note')),
  
  -- Entity linking (optional)
  entity_type TEXT,  -- person, company, vendor, project, deal, candidate, etc.
  entity_id UUID,    -- FK to specific entity table (flexible)
  
  -- Content & metadata
  title TEXT NOT NULL,
  content TEXT NOT NULL,  -- cleaned, used for embedding
  raw_payload JSONB,      -- full structured data, tool outputs, metadata
  
  -- Source tracking
  source TEXT NOT NULL DEFAULT 'workflow'
    CHECK (source IN ('workflow', 'chat', 'import', 'system')),
  
  -- Importance & decay
  importance INT DEFAULT 50 CHECK (importance >= 0 AND importance <= 100),
  
  -- Status & expiration
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'deprecated', 'archived')),
  expires_at TIMESTAMP,  -- for state memory / short-lived notes
  
  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  
  -- Indexes
  CONSTRAINT memory_items_org_id_index UNIQUE (org_id, id)
);

CREATE INDEX idx_memory_items_org_type_created 
  ON memory_items(org_id, memory_type, created_at DESC);
  
CREATE INDEX idx_memory_items_org_entity 
  ON memory_items(org_id, entity_type, entity_id, created_at DESC);
  
CREATE INDEX idx_memory_items_workspace 
  ON memory_items(workspace_id, memory_type);
  
CREATE INDEX idx_memory_items_status 
  ON memory_items(org_id, status, created_at DESC);

-- ============================================================================
-- TABLE: memory_vectors (Vector Index for Semantic Search)
-- ============================================================================
-- Stores embeddings for memory_items
-- ============================================================================

CREATE TABLE IF NOT EXISTS memory_vectors (
  memory_id UUID PRIMARY KEY REFERENCES memory_items(id) ON DELETE CASCADE,
  org_id UUID NOT NULL,
  
  -- Vector embedding (1536 for OpenAI text-embedding-3-small, 3072 for large)
  embedding VECTOR(1536) NOT NULL,
  embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
  
  -- Metadata
  embedded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  
  CONSTRAINT fk_memory_vectors_org FOREIGN KEY (org_id) 
    REFERENCES organizations(id) ON DELETE CASCADE
);

-- Vector index for fast ANN search (HNSW or IVFFLAT)
CREATE INDEX idx_memory_vectors_hnsw 
  ON memory_vectors USING hnsw (embedding vector_cosine_ops);
  
-- Regular index for org scoping
CREATE INDEX idx_memory_vectors_org 
  ON memory_vectors(org_id);

-- ============================================================================
-- TABLE: workflow_states (Short-Term Working Memory / Checkpointing)
-- ============================================================================
-- Stores workflow execution state for resumption (LangGraph-compatible)
-- ============================================================================

CREATE TABLE IF NOT EXISTS workflow_states (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  
  -- Multi-tenancy
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
  
  -- Workflow identification
  workflow_id TEXT NOT NULL,  -- unique workflow identifier
  thread_id TEXT NOT NULL,    -- per-user session thread
  
  -- State data (LangGraph checkpoint format)
  state JSONB NOT NULL,  -- entire workflow state (current step, variables, tool results)
  
  -- Execution tracking
  step TEXT,              -- current step name
  status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running', 'paused', 'completed', 'failed')),
  
  -- Error tracking (for failed workflows)
  error_message TEXT,
  
  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  
  -- Constraints
  CONSTRAINT unique_workflow_thread 
    UNIQUE (org_id, workflow_id, thread_id)
);

CREATE INDEX idx_workflow_states_org 
  ON workflow_states(org_id, workflow_id);
  
CREATE INDEX idx_workflow_states_thread 
  ON workflow_states(org_id, thread_id, updated_at DESC);
  
CREATE INDEX idx_workflow_states_status 
  ON workflow_states(org_id, status);

-- ============================================================================
-- RLS POLICIES (Row-Level Security for Multi-Tenancy)
-- ============================================================================

-- Enable RLS
ALTER TABLE memory_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_states ENABLE ROW LEVEL SECURITY;

-- Helper function: Check org membership
CREATE OR REPLACE FUNCTION is_org_member(p_org_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
  RETURN EXISTS (
    SELECT 1 FROM org_memberships 
    WHERE org_id = p_org_id AND user_id = auth.uid()
  ) OR EXISTS (
    SELECT 1 FROM organizations 
    WHERE id = p_org_id AND owner_id = auth.uid()
  );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- memory_items RLS policies
CREATE POLICY memory_items_select ON memory_items FOR SELECT
  USING (is_org_member(org_id));

CREATE POLICY memory_items_insert ON memory_items FOR INSERT
  WITH CHECK (is_org_member(org_id));

CREATE POLICY memory_items_update ON memory_items FOR UPDATE
  USING (is_org_member(org_id));

CREATE POLICY memory_items_delete ON memory_items FOR DELETE
  USING (is_org_member(org_id) AND created_by = auth.uid());

-- memory_vectors RLS policies
CREATE POLICY memory_vectors_select ON memory_vectors FOR SELECT
  USING (is_org_member(org_id));

CREATE POLICY memory_vectors_insert ON memory_vectors FOR INSERT
  WITH CHECK (is_org_member(org_id));

CREATE POLICY memory_vectors_update ON memory_vectors FOR UPDATE
  USING (is_org_member(org_id));

-- workflow_states RLS policies
CREATE POLICY workflow_states_select ON workflow_states FOR SELECT
  USING (is_org_member(org_id));

CREATE POLICY workflow_states_insert ON workflow_states FOR INSERT
  WITH CHECK (is_org_member(org_id));

CREATE POLICY workflow_states_update ON workflow_states FOR UPDATE
  USING (is_org_member(org_id));

CREATE POLICY workflow_states_delete ON workflow_states FOR DELETE
  USING (is_org_member(org_id));

-- ============================================================================
-- RPC FUNCTION: match_memories (Hybrid Search)
-- ============================================================================
-- Performs semantic search + metadata filtering for memory retrieval
-- ============================================================================

CREATE OR REPLACE FUNCTION match_memories(
  p_org_id UUID,
  p_query_embedding VECTOR(1536),
  p_memory_type TEXT DEFAULT NULL,
  p_entity_type TEXT DEFAULT NULL,
  p_entity_id UUID DEFAULT NULL,
  p_limit INT DEFAULT 5,
  p_threshold FLOAT DEFAULT 0.5
)
RETURNS TABLE (
  memory_id UUID,
  title TEXT,
  content TEXT,
  memory_type TEXT,
  entity_type TEXT,
  entity_id UUID,
  importance INT,
  created_at TIMESTAMP,
  similarity FLOAT
) AS $$
BEGIN
  RETURN QUERY
  SELECT 
    mi.id,
    mi.title,
    mi.content,
    mi.memory_type,
    mi.entity_type,
    mi.entity_id,
    mi.importance,
    mi.created_at,
    (1 - (mv.embedding <=> p_query_embedding)) AS similarity
  FROM memory_items mi
  JOIN memory_vectors mv ON mi.id = mv.memory_id
  WHERE mi.org_id = p_org_id
    AND mi.status = 'active'
    AND (p_memory_type IS NULL OR mi.memory_type = p_memory_type)
    AND (p_entity_type IS NULL OR mi.entity_type = p_entity_type)
    AND (p_entity_id IS NULL OR mi.entity_id = p_entity_id)
    AND (1 - (mv.embedding <=> p_query_embedding)) > p_threshold
  ORDER BY 
    similarity DESC,
    mi.created_at DESC
  LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- RPC FUNCTION: get_entity_timeline (Entity-Specific Memory Retrieval)
-- ============================================================================
-- Retrieves all memories linked to a specific entity

CREATE OR REPLACE FUNCTION get_entity_timeline(
  p_org_id UUID,
  p_entity_type TEXT,
  p_entity_id UUID,
  p_limit INT DEFAULT 20
)
RETURNS TABLE (
  memory_id UUID,
  title TEXT,
  content TEXT,
  memory_type TEXT,
  importance INT,
  created_at TIMESTAMP
) AS $$
BEGIN
  RETURN QUERY
  SELECT 
    mi.id,
    mi.title,
    mi.content,
    mi.memory_type,
    mi.importance,
    mi.created_at
  FROM memory_items mi
  WHERE mi.org_id = p_org_id
    AND mi.entity_type = p_entity_type
    AND mi.entity_id = p_entity_id
    AND mi.status = 'active'
  ORDER BY mi.created_at DESC
  LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- RPC FUNCTION: dedup_memories (Consolidation)
-- ============================================================================
-- Deprecates old memories if new ones are similar (similarity > 0.92)

CREATE OR REPLACE FUNCTION dedup_memories(
  p_org_id UUID,
  p_memory_id UUID,
  p_similarity_threshold FLOAT DEFAULT 0.92
)
RETURNS INT AS $$
DECLARE
  v_count INT;
  v_embedding VECTOR(1536);
BEGIN
  -- Get embedding of the new memory
  SELECT mv.embedding INTO v_embedding
  FROM memory_vectors mv
  WHERE mv.memory_id = p_memory_id AND mv.org_id = p_org_id;
  
  IF v_embedding IS NULL THEN
    RETURN 0;
  END IF;
  
  -- Deprecate old similar memories
  UPDATE memory_items
  SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP
  WHERE org_id = p_org_id
    AND id != p_memory_id
    AND status = 'active'
    AND id IN (
      SELECT memory_id FROM memory_vectors
      WHERE org_id = p_org_id
        AND (1 - (embedding <=> v_embedding)) > p_similarity_threshold
    );
  
  GET DIAGNOSTICS v_count = ROW_COUNT;
  RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- TRIGGERS: Auto-update updated_at
-- ============================================================================

CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_memory_items_updated 
  BEFORE UPDATE ON memory_items
  FOR EACH ROW
  EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER trg_workflow_states_updated 
  BEFORE UPDATE ON workflow_states
  FOR EACH ROW
  EXECUTE FUNCTION update_timestamp();

-- ============================================================================
-- SAMPLE DATA (for testing)
-- ============================================================================

INSERT INTO memory_items (
  org_id, workspace_id, created_by, memory_type, entity_type,
  title, content, source, importance, status
) VALUES 
(
  (SELECT id FROM organizations LIMIT 1),
  (SELECT id FROM workspaces LIMIT 1),
  auth.uid(),
  'semantic',
  'company',
  'Client Acme prefers Slack communication',
  'Client Acme has explicitly stated they prefer Slack over email for urgent updates. Morning meetings are not preferred, schedule after 1 PM.',
  'workflow',
  85,
  'active'
),
(
  (SELECT id FROM organizations LIMIT 1),
  (SELECT id FROM workspaces LIMIT 1),
  auth.uid(),
  'preference',
  NULL,
  'Auto-approval threshold',
  'Invoices under $1000 can be auto-approved. Above $1000 requires CFO review.',
  'system',
  90,
  'active'
),
(
  (SELECT id FROM organizations LIMIT 1),
  (SELECT id FROM workspaces LIMIT 1),
  auth.uid(),
  'episode',
  'candidate',
  'Candidate John hired as SDE',
  'Successfully hired John as Senior Software Engineer. Onboarding completed. Tech stack: Python, Go, Kubernetes.',
  'workflow',
  75,
  'active'
);

-- ============================================================================
-- END OF MIGRATION
-- ============================================================================
