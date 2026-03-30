-- ============================================================================
-- PHASE 2: STATES LAYER DATABASE SCHEMA
-- Purpose: Crash-proof workflow state management with deterministic resume
-- Database: Supabase (PostgreSQL)
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- 1. WORKFLOW_STATES TABLE (Current snapshot per session)
-- ============================================================================
-- Stores the latest state of each workflow session
-- Used for quick UI listing and resumption starting point

CREATE TABLE IF NOT EXISTS workflow_states (
  -- Identifiers
  session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL,
  user_id UUID,
  org_id UUID NOT NULL,
  
  -- Execution metadata
  title TEXT,
  status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'completed', 'failed', 'cancelled')),
  current_node TEXT NOT NULL,
  last_node TEXT,
  priority TEXT DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high')),
  
  -- The brain: full context snapshot
  state_data JSONB NOT NULL DEFAULT '{}',
  
  -- Concurrency control
  version INT DEFAULT 1,
  last_event_seq BIGINT DEFAULT 0,
  
  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  last_event_at TIMESTAMPTZ DEFAULT NOW(),
  
  -- Constraints
  CONSTRAINT valid_state_data CHECK (jsonb_typeof(state_data) = 'object')
);

-- Indexes for fast queries
CREATE INDEX idx_workflow_states_org_status ON workflow_states (org_id, status, updated_at DESC);
CREATE INDEX idx_workflow_states_workspace_node ON workflow_states (workspace_id, current_node, status, updated_at DESC);
CREATE INDEX idx_workflow_states_session_id ON workflow_states (session_id);
CREATE INDEX idx_workflow_states_user_sessions ON workflow_states (workspace_id, user_id, updated_at DESC);

-- Enable RLS on workflow_states
ALTER TABLE workflow_states ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Users can only read sessions from their workspace
CREATE POLICY workflow_states_select_policy ON workflow_states
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM workspace_memberships wm
      WHERE wm.workspace_id = workflow_states.workspace_id
      AND wm.user_id = auth.uid()
    )
  );

-- RLS Policy: Only service_role can write
CREATE POLICY workflow_states_write_policy ON workflow_states
  FOR INSERT
  WITH CHECK (auth.role() = 'service_role');

CREATE POLICY workflow_states_update_policy ON workflow_states
  FOR UPDATE
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 2. WORKFLOW_STATE_EVENTS TABLE (Append-only event log)
-- ============================================================================
-- Immutable event stream for deterministic replay and audit
-- Events must be applied in sequence order

CREATE TABLE IF NOT EXISTS workflow_state_events (
  -- Identifiers
  event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES workflow_states(session_id) ON DELETE CASCADE,
  workspace_id UUID NOT NULL,
  user_id UUID,
  org_id UUID NOT NULL,
  
  -- Event metadata
  event_type TEXT NOT NULL CHECK (event_type IN (
    'node_entered', 'node_exited',
    'tool_called', 'tool_result', 'tool_failed',
    'approval_requested', 'approval_decision',
    'user_message', 'ai_message',
    'checkpoint', 'error', 'state_updated'
  )),
  node TEXT,
  
  -- Event ordering (strict monotonic sequence)
  sequence BIGINT NOT NULL,
  
  -- Idempotency (prevent double execution on retries)
  idempotency_key TEXT,
  
  -- Event payload (what happened)
  payload JSONB NOT NULL DEFAULT '{}',
  
  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  
  -- Constraints
  CONSTRAINT valid_sequence CHECK (sequence > 0),
  CONSTRAINT valid_payload CHECK (jsonb_typeof(payload) = 'object'),
  UNIQUE(session_id, sequence),
  UNIQUE(session_id, idempotency_key) WHERE idempotency_key IS NOT NULL
);

-- Indexes for replay and audit
CREATE INDEX idx_workflow_state_events_session_seq ON workflow_state_events (session_id, sequence ASC);
CREATE INDEX idx_workflow_state_events_workspace_created ON workflow_state_events (workspace_id, created_at DESC);
CREATE INDEX idx_workflow_state_events_idempotency ON workflow_state_events (session_id, idempotency_key);
CREATE INDEX idx_workflow_state_events_type ON workflow_state_events (event_type, created_at DESC);

-- Enable RLS on workflow_state_events
ALTER TABLE workflow_state_events ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Users can only read events from their workspace sessions
CREATE POLICY workflow_state_events_select_policy ON workflow_state_events
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM workspace_memberships wm
      WHERE wm.workspace_id = workflow_state_events.workspace_id
      AND wm.user_id = auth.uid()
    )
  );

-- RLS Policy: Only service_role can insert events
CREATE POLICY workflow_state_events_insert_policy ON workflow_state_events
  FOR INSERT
  WITH CHECK (auth.role() = 'service_role');

-- RLS Policy: Events cannot be updated or deleted (immutable audit trail)
CREATE POLICY workflow_state_events_update_deny_policy ON workflow_state_events
  FOR UPDATE
  USING (FALSE);

CREATE POLICY workflow_state_events_delete_deny_policy ON workflow_state_events
  FOR DELETE
  USING (FALSE);

-- ============================================================================
-- 3. WORKFLOW_STATE_LOCKS TABLE (Lease-based single executor guarantee)
-- ============================================================================
-- Ensures only one worker executes a session at a time
-- Lease expires after locked_until timestamp

CREATE TABLE IF NOT EXISTS workflow_state_locks (
  -- Lock identifiers
  session_id UUID PRIMARY KEY REFERENCES workflow_states(session_id) ON DELETE CASCADE,
  workspace_id UUID NOT NULL,
  
  -- Lock ownership
  locked_by TEXT NOT NULL,  -- Worker ID / instance identifier
  lock_token UUID NOT NULL DEFAULT gen_random_uuid(),
  
  -- Lease parameters
  locked_until TIMESTAMPTZ NOT NULL,
  heartbeat_at TIMESTAMPTZ DEFAULT NOW(),
  
  -- Diagnostics
  attempt INT DEFAULT 1,
  metadata JSONB DEFAULT '{}',
  
  -- Timestamps
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  
  -- Constraints
  CONSTRAINT valid_lock_duration CHECK (locked_until > locked_at)
);

-- Indexes for lock queries
CREATE INDEX idx_workflow_state_locks_workspace_expires ON workflow_state_locks (workspace_id, locked_until DESC);
CREATE INDEX idx_workflow_state_locks_locked_by ON workflow_state_locks (locked_by, locked_until DESC);
CREATE INDEX idx_workflow_state_locks_session ON workflow_state_locks (session_id);

-- Enable RLS on workflow_state_locks
ALTER TABLE workflow_state_locks ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Only service_role can manage locks
CREATE POLICY workflow_state_locks_all_policy ON workflow_state_locks
  FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- ============================================================================
-- 4. TRIGGER: Auto-update updated_at on workflow_states
-- ============================================================================

CREATE OR REPLACE FUNCTION update_workflow_states_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS workflow_states_updated_at_trigger ON workflow_states;
CREATE TRIGGER workflow_states_updated_at_trigger
  BEFORE UPDATE ON workflow_states
  FOR EACH ROW
  EXECUTE FUNCTION update_workflow_states_timestamp();

-- ============================================================================
-- 5. RPC FUNCTIONS (Atomic database operations)
-- ============================================================================

-- 5.1 ACQUIRE_WORKFLOW_LOCK
-- Atomically acquire a lease lock for a session
-- Returns lock_token and locked_until if successful, empty if lock held

CREATE OR REPLACE FUNCTION public.acquire_workflow_lock(
  p_session_id UUID,
  p_workspace_id UUID,
  p_locked_by TEXT,
  p_lease_seconds INT DEFAULT 30
)
RETURNS TABLE (
  lock_token UUID,
  locked_until TIMESTAMPTZ
) AS $$
DECLARE
  v_now TIMESTAMPTZ := NOW();
  v_lock_expires TIMESTAMPTZ := v_now + (p_lease_seconds || ' seconds')::INTERVAL;
  v_existing_lock RECORD;
BEGIN
  -- Check if lock exists and is still valid
  SELECT * INTO v_existing_lock
  FROM workflow_state_locks
  WHERE session_id = p_session_id;
  
  -- If valid lock exists and not expired, return nothing (lock already held)
  IF v_existing_lock IS NOT NULL AND v_existing_lock.locked_until > v_now THEN
    RETURN;
  END IF;
  
  -- Lock is expired or doesn't exist, acquire it
  INSERT INTO workflow_state_locks (
    session_id, workspace_id, locked_by, lock_token, locked_until, heartbeat_at
  ) VALUES (
    p_session_id, p_workspace_id, p_locked_by, gen_random_uuid(), v_lock_expires, v_now
  )
  ON CONFLICT (session_id) DO UPDATE
  SET 
    locked_by = EXCLUDED.locked_by,
    lock_token = EXCLUDED.lock_token,
    locked_until = EXCLUDED.locked_until,
    heartbeat_at = EXCLUDED.heartbeat_at,
    attempt = COALESCE(workflow_state_locks.attempt, 0) + 1
  WHERE workflow_state_locks.locked_until <= v_now
  RETURNING lock_token, locked_until;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 5.2 RENEW_WORKFLOW_LOCK
-- Renew an existing lock if token matches (heartbeat)
-- Returns true if renewed, false if token mismatch or lock expired

CREATE OR REPLACE FUNCTION public.renew_workflow_lock(
  p_session_id UUID,
  p_lock_token UUID,
  p_lease_seconds INT DEFAULT 30
)
RETURNS BOOLEAN AS $$
DECLARE
  v_now TIMESTAMPTZ := NOW();
  v_new_expires TIMESTAMPTZ := v_now + (p_lease_seconds || ' seconds')::INTERVAL;
  v_updated_rows INT;
BEGIN
  UPDATE workflow_state_locks
  SET 
    locked_until = v_new_expires,
    heartbeat_at = v_now
  WHERE session_id = p_session_id
    AND lock_token = p_lock_token
    AND locked_until > v_now;
  
  v_updated_rows := ROW_COUNT;
  RETURN v_updated_rows > 0;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 5.3 RELEASE_WORKFLOW_LOCK
-- Release lock if token matches
-- Returns true if released, false if token mismatch

CREATE OR REPLACE FUNCTION public.release_workflow_lock(
  p_session_id UUID,
  p_lock_token UUID
)
RETURNS BOOLEAN AS $$
DECLARE
  v_deleted_rows INT;
BEGIN
  DELETE FROM workflow_state_locks
  WHERE session_id = p_session_id
    AND lock_token = p_lock_token;
  
  v_deleted_rows := ROW_COUNT;
  RETURN v_deleted_rows > 0;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 5.4 APPEND_WORKFLOW_EVENT
-- Atomically append event and return next sequence
-- Fails if session doesn't exist or idempotency_key duplicates

CREATE OR REPLACE FUNCTION public.append_workflow_event(
  p_session_id UUID,
  p_workspace_id UUID,
  p_user_id UUID,
  p_org_id UUID,
  p_event_type TEXT,
  p_node TEXT,
  p_idempotency_key TEXT DEFAULT NULL,
  p_payload JSONB DEFAULT '{}'::JSONB
)
RETURNS TABLE (
  event_id UUID,
  sequence BIGINT
) AS $$
DECLARE
  v_next_seq BIGINT;
  v_new_event_id UUID;
BEGIN
  -- Get next sequence for this session
  v_next_seq := COALESCE((
    SELECT MAX(sequence) FROM workflow_state_events
    WHERE session_id = p_session_id
  ), 0) + 1;
  
  -- Insert event atomically
  INSERT INTO workflow_state_events (
    session_id, workspace_id, user_id, org_id,
    event_type, node, sequence, idempotency_key, payload
  ) VALUES (
    p_session_id, p_workspace_id, p_user_id, p_org_id,
    p_event_type, p_node, v_next_seq, p_idempotency_key, p_payload
  )
  RETURNING workflow_state_events.event_id INTO v_new_event_id;
  
  RETURN QUERY SELECT v_new_event_id, v_next_seq;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 5.5 GET_EVENTS_SINCE
-- Retrieve events for replay starting after last_seq
-- Used for delta hydration during resume

CREATE OR REPLACE FUNCTION public.get_events_since(
  p_session_id UUID,
  p_last_sequence BIGINT DEFAULT 0,
  p_limit INT DEFAULT 500
)
RETURNS TABLE (
  event_id UUID,
  event_type TEXT,
  node TEXT,
  sequence BIGINT,
  payload JSONB,
  created_at TIMESTAMPTZ
) AS $$
BEGIN
  RETURN QUERY
  SELECT 
    e.event_id, e.event_type, e.node, e.sequence, e.payload, e.created_at
  FROM workflow_state_events e
  WHERE e.session_id = p_session_id
    AND e.sequence > p_last_sequence
  ORDER BY e.sequence ASC
  LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- 6. SAMPLE DATA (For testing)
-- ============================================================================

-- Insert sample workspace membership for RLS testing (if needed)
-- (Assumes you have workspace_memberships table with user_id, workspace_id, role columns)

-- ============================================================================
-- 7. MIGRATION METADATA
-- ============================================================================

-- Record this migration
INSERT INTO schema_migrations (name, description, created_at)
VALUES (
  'PHASE2_STATES_MIGRATION',
  'Crash-proof workflow state management with deterministic resume',
  NOW()
)
ON CONFLICT DO NOTHING;

-- ============================================================================
-- END OF MIGRATION
-- ============================================================================
-- Tables: 3 (workflow_states, workflow_state_events, workflow_state_locks)
-- RPC Functions: 5 (acquire, renew, release, append_event, get_events_since)
-- RLS Policies: 8 (select, insert, update on states; insert-only on events; all on locks)
-- Triggers: 1 (updated_at on workflow_states)
-- Indexes: 10 (performance optimization for common queries)
-- ============================================================================
