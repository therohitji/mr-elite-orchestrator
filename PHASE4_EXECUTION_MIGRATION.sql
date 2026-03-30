-- =====================================================================
-- PHASE 4: EXECUTION LAYER - DATABASE SCHEMA
-- =====================================================================
-- This migration creates the persistence layer for Phase 4 Execution.
-- It stores execution results, logs, and manages retry queues.
--
-- Per spec:
--   - Result Capture: Store all execution outputs linked to atoms
--   - Error Recovery: Dead letter queue + retry tracking
--   - Workflow Resumption: Checkpoint management for pause/resume
-- =====================================================================

-- =====================================================================
-- TABLE 1: execution_results (The Black Box for All Executions)
-- =====================================================================
-- Stores the output of every executed atom
-- Immutable once written (audit trail)

CREATE TABLE IF NOT EXISTS execution_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Identity & Context
    atom_id UUID NOT NULL,
    request_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    
    -- Execution Details
    tool_name TEXT NOT NULL,
    runway_type TEXT NOT NULL CHECK (runway_type IN ('CODE_TOOL', 'AI_AGENT', 'WORKFLOW')),
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED', 'TIMEOUT', 'RETRYING')),
    
    -- Results & Errors
    output JSONB,  -- Successful execution output
    error TEXT,    -- Error message if failed
    
    -- Metadata
    duration_ms FLOAT DEFAULT 0.0,
    retries INT DEFAULT 0,
    trace_id TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    
    -- Indexes for fast queries
    CONSTRAINT execution_results_atom_unique UNIQUE (atom_id, created_at)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_execution_results_org_status 
    ON execution_results (org_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_execution_results_request 
    ON execution_results (request_id);
CREATE INDEX IF NOT EXISTS idx_execution_results_atom 
    ON execution_results (atom_id);
CREATE INDEX IF NOT EXISTS idx_execution_results_created 
    ON execution_results (created_at DESC);

-- Enable RLS
ALTER TABLE execution_results ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Users see only their org's results
CREATE POLICY execution_results_org_isolation ON execution_results
    FOR ALL
    USING (org_id = current_setting('app.org_id', true));


-- =====================================================================
-- TABLE 2: retry_queue (Failed Atoms Waiting for Retry)
-- =====================================================================
-- Manages exponential backoff retry logic
-- Atoms remain here until max retries exhausted or success

CREATE TABLE IF NOT EXISTS retry_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Atom Identity
    atom_id UUID NOT NULL,
    request_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    
    -- Retry Logic
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    next_retry_at TIMESTAMPTZ NOT NULL,
    
    -- Original Execution Details
    tool_name TEXT NOT NULL,
    tool_arguments JSONB NOT NULL,
    runway_type TEXT NOT NULL,
    
    -- Failure Info
    last_error TEXT,
    failure_reason TEXT,
    
    -- Status
    status TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'RETRYING', 'EXHAUSTED', 'SUCCESS')),
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_retry_queue_next_retry 
    ON retry_queue (next_retry_at) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_retry_queue_atom 
    ON retry_queue (atom_id);
CREATE INDEX IF NOT EXISTS idx_retry_queue_org_status 
    ON retry_queue (org_id, workspace_id, status);

-- Enable RLS
ALTER TABLE retry_queue ENABLE ROW LEVEL SECURITY;

-- RLS Policy
CREATE POLICY retry_queue_org_isolation ON retry_queue
    FOR ALL
    USING (org_id = current_setting('app.org_id', true));


-- =====================================================================
-- TABLE 3: workflow_checkpoints (State for Long-Running Workflows)
-- =====================================================================
-- Stores execution state for pause/resume functionality
-- Allows recovery after system failures

CREATE TABLE IF NOT EXISTS workflow_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Workflow Identity
    request_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    
    -- Execution State
    completed_atoms JSONB NOT NULL DEFAULT '[]'::JSONB,  -- List of completed atom IDs
    pending_atoms JSONB NOT NULL DEFAULT '[]'::JSONB,    -- List of pending atom IDs
    failed_atoms JSONB NOT NULL DEFAULT '[]'::JSONB,     -- List of failed atom IDs
    
    -- Global Context Snapshot
    global_context JSONB NOT NULL DEFAULT '{}'::JSONB,
    
    -- Status
    status TEXT DEFAULT 'IN_PROGRESS' CHECK (status IN ('IN_PROGRESS', 'PAUSED', 'COMPLETED', 'FAILED')),
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT workflow_checkpoints_request_unique UNIQUE (request_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_workflow_checkpoints_org_status 
    ON workflow_checkpoints (org_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_workflow_checkpoints_updated 
    ON workflow_checkpoints (updated_at DESC);

-- Enable RLS
ALTER TABLE workflow_checkpoints ENABLE ROW LEVEL SECURITY;

-- RLS Policy
CREATE POLICY workflow_checkpoints_org_isolation ON workflow_checkpoints
    FOR ALL
    USING (org_id = current_setting('app.org_id', true));


-- =====================================================================
-- TABLE 4: execution_logs (Detailed Execution Trace)
-- =====================================================================
-- Granular logging of execution events (start, step, complete)
-- Useful for debugging and observability

CREATE TABLE IF NOT EXISTS execution_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Context
    atom_id UUID NOT NULL,
    request_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    
    -- Log Entry
    event_type TEXT NOT NULL CHECK (event_type IN ('START', 'STEP', 'COMPLETE', 'ERROR', 'RETRY')),
    message TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::JSONB,
    
    -- Timestamp
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_execution_logs_atom 
    ON execution_logs (atom_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_logs_request 
    ON execution_logs (request_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_logs_event 
    ON execution_logs (event_type, created_at DESC);

-- Enable RLS
ALTER TABLE execution_logs ENABLE ROW LEVEL SECURITY;

-- RLS Policy
CREATE POLICY execution_logs_org_isolation ON execution_logs
    FOR ALL
    USING (org_id = current_setting('app.org_id', true));


-- =====================================================================
-- RPC FUNCTION 1: store_execution_result
-- =====================================================================
-- Atomically stores an execution result
-- Returns the result ID

CREATE OR REPLACE FUNCTION store_execution_result(
    p_atom_id UUID,
    p_request_id TEXT,
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_tool_name TEXT,
    p_runway_type TEXT,
    p_status TEXT,
    p_output JSONB DEFAULT NULL,
    p_error TEXT DEFAULT NULL,
    p_duration_ms FLOAT DEFAULT 0.0,
    p_retries INT DEFAULT 0,
    p_trace_id TEXT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    v_result_id UUID;
BEGIN
    INSERT INTO execution_results (
        atom_id, request_id, org_id, workspace_id,
        tool_name, runway_type, status,
        output, error, duration_ms, retries, trace_id,
        completed_at
    ) VALUES (
        p_atom_id, p_request_id, p_org_id, p_workspace_id,
        p_tool_name, p_runway_type, p_status,
        p_output, p_error, p_duration_ms, p_retries, p_trace_id,
        NOW()
    )
    RETURNING id INTO v_result_id;
    
    RETURN v_result_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 2: enqueue_retry
-- =====================================================================
-- Add a failed atom to retry queue with exponential backoff

CREATE OR REPLACE FUNCTION enqueue_retry(
    p_atom_id UUID,
    p_request_id TEXT,
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_tool_name TEXT,
    p_tool_arguments JSONB,
    p_runway_type TEXT,
    p_last_error TEXT,
    p_retry_count INT DEFAULT 0,
    p_max_retries INT DEFAULT 3
) RETURNS UUID AS $$
DECLARE
    v_queue_id UUID;
    v_backoff_seconds INT;
BEGIN
    -- Calculate exponential backoff: 2^retry_count minutes
    v_backoff_seconds := POWER(2, p_retry_count) * 60;
    
    INSERT INTO retry_queue (
        atom_id, request_id, org_id, workspace_id,
        tool_name, tool_arguments, runway_type,
        retry_count, max_retries, next_retry_at,
        last_error, status
    ) VALUES (
        p_atom_id, p_request_id, p_org_id, p_workspace_id,
        p_tool_name, p_tool_arguments, p_runway_type,
        p_retry_count, p_max_retries, NOW() + (v_backoff_seconds || ' seconds')::INTERVAL,
        p_last_error, 'PENDING'
    )
    ON CONFLICT (atom_id) 
    DO UPDATE SET
        retry_count = p_retry_count,
        next_retry_at = NOW() + (v_backoff_seconds || ' seconds')::INTERVAL,
        last_error = p_last_error,
        updated_at = NOW()
    RETURNING id INTO v_queue_id;
    
    RETURN v_queue_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 3: get_pending_retries
-- =====================================================================
-- Fetch atoms ready for retry (next_retry_at <= NOW)

CREATE OR REPLACE FUNCTION get_pending_retries(
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_limit INT DEFAULT 10
) RETURNS TABLE (
    id UUID,
    atom_id UUID,
    request_id TEXT,
    tool_name TEXT,
    tool_arguments JSONB,
    runway_type TEXT,
    retry_count INT,
    max_retries INT,
    last_error TEXT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        rq.id, rq.atom_id, rq.request_id, rq.tool_name,
        rq.tool_arguments, rq.runway_type, rq.retry_count,
        rq.max_retries, rq.last_error
    FROM retry_queue rq
    WHERE rq.org_id = p_org_id
      AND rq.workspace_id = p_workspace_id
      AND rq.status = 'PENDING'
      AND rq.next_retry_at <= NOW()
      AND rq.retry_count < rq.max_retries
    ORDER BY rq.next_retry_at ASC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 4: save_checkpoint
-- =====================================================================
-- Save workflow execution state for resumption

CREATE OR REPLACE FUNCTION save_checkpoint(
    p_request_id TEXT,
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_completed_atoms JSONB,
    p_pending_atoms JSONB,
    p_failed_atoms JSONB,
    p_global_context JSONB,
    p_status TEXT DEFAULT 'IN_PROGRESS'
) RETURNS UUID AS $$
DECLARE
    v_checkpoint_id UUID;
BEGIN
    INSERT INTO workflow_checkpoints (
        request_id, org_id, workspace_id,
        completed_atoms, pending_atoms, failed_atoms,
        global_context, status, updated_at
    ) VALUES (
        p_request_id, p_org_id, p_workspace_id,
        p_completed_atoms, p_pending_atoms, p_failed_atoms,
        p_global_context, p_status, NOW()
    )
    ON CONFLICT (request_id)
    DO UPDATE SET
        completed_atoms = p_completed_atoms,
        pending_atoms = p_pending_atoms,
        failed_atoms = p_failed_atoms,
        global_context = p_global_context,
        status = p_status,
        updated_at = NOW()
    RETURNING id INTO v_checkpoint_id;
    
    RETURN v_checkpoint_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 5: log_execution_event
-- =====================================================================
-- Log a single execution event for observability

CREATE OR REPLACE FUNCTION log_execution_event(
    p_atom_id UUID,
    p_request_id TEXT,
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_event_type TEXT,
    p_message TEXT,
    p_metadata JSONB DEFAULT '{}'::JSONB
) RETURNS UUID AS $$
DECLARE
    v_log_id UUID;
BEGIN
    INSERT INTO execution_logs (
        atom_id, request_id, org_id, workspace_id,
        event_type, message, metadata
    ) VALUES (
        p_atom_id, p_request_id, p_org_id, p_workspace_id,
        p_event_type, p_message, p_metadata
    )
    RETURNING id INTO v_log_id;
    
    RETURN v_log_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 6: get_execution_results
-- =====================================================================
-- Query execution results for a request

CREATE OR REPLACE FUNCTION get_execution_results(
    p_request_id TEXT,
    p_org_id TEXT,
    p_workspace_id TEXT
) RETURNS TABLE (
    id UUID,
    atom_id UUID,
    tool_name TEXT,
    runway_type TEXT,
    status TEXT,
    output JSONB,
    error TEXT,
    duration_ms FLOAT,
    retries INT,
    created_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        er.id, er.atom_id, er.tool_name, er.runway_type,
        er.status, er.output, er.error, er.duration_ms,
        er.retries, er.created_at, er.completed_at
    FROM execution_results er
    WHERE er.request_id = p_request_id
      AND er.org_id = p_org_id
      AND er.workspace_id = p_workspace_id
    ORDER BY er.created_at ASC;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- SAMPLE DATA (For Testing)
-- =====================================================================

-- Example: Store a successful execution result
-- SELECT store_execution_result(
--     'a1b2c3d4-e5f6-7890-abcd-ef1234567890'::UUID,  -- atom_id
--     'req_test_001',                                 -- request_id
--     'org_demo',                                     -- org_id
--     'ws_default',                                   -- workspace_id
--     'send_email',                                   -- tool_name
--     'CODE_TOOL',                                    -- runway_type
--     'SUCCESS',                                      -- status
--     '{"message_id": "msg_123", "sent_at": "2026-01-10T10:00:00Z"}'::JSONB,  -- output
--     NULL,                                           -- error
--     125.5,                                          -- duration_ms
--     0,                                              -- retries
--     'trace_xyz789'                                  -- trace_id
-- );

-- =====================================================================
-- END OF MIGRATION
-- =====================================================================
