-- =====================================================================
-- PHASE 5: EVALUATION LAYER - DATABASE SCHEMA
-- =====================================================================
-- This migration creates the learning and analytics infrastructure.
-- It enables MDS calculation, pattern recognition, and smart recommendations.
--
-- Per spec:
--   - Analytics tracking (costs, latency, tool usage)
--   - MDS Engine (Maturity Delta Score - time saved)
--   - Smart recommendations (workflow discovery, policy evolution)
-- =====================================================================

-- =====================================================================
-- TABLE 1: task_effort_benchmarks (Reference Library for MDS)
-- =====================================================================
-- Stores "standard human time" for various business tasks
-- Used to calculate time saved (MDS score)

CREATE TABLE IF NOT EXISTS task_effort_benchmarks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Task Identity
    task_type TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK (category IN ('ADMIN', 'HR', 'SALES', 'DEV', 'FINANCE', 'GENERAL')),
    
    -- Time Estimates
    manual_minutes_est FLOAT NOT NULL DEFAULT 5.0,
    ai_minutes_est FLOAT NOT NULL DEFAULT 0.1,
    
    -- Scoring
    complexity_weight FLOAT NOT NULL DEFAULT 1.0,
    
    -- Metadata
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Sample data
INSERT INTO task_effort_benchmarks (task_type, category, manual_minutes_est, ai_minutes_est, complexity_weight, description) VALUES
    ('send_email', 'ADMIN', 5.0, 0.1, 1.0, 'Compose and send email'),
    ('draft_offer', 'HR', 45.0, 2.0, 2.0, 'Draft job offer letter'),
    ('check_hiring_budget', 'HR', 10.0, 0.2, 1.0, 'Query budget availability'),
    ('fetch_job_template', 'HR', 3.0, 0.1, 1.0, 'Retrieve template from database'),
    ('setup_access', 'DEV', 15.0, 0.5, 1.5, 'Provision user access'),
    ('approve_payment', 'FINANCE', 10.0, 0.3, 2.0, 'Review and approve financial transaction'),
    ('confirm_offer_acceptance', 'HR', 5.0, 0.2, 1.0, 'Record candidate acceptance')
ON CONFLICT (task_type) DO NOTHING;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_benchmarks_category ON task_effort_benchmarks (category);


-- =====================================================================
-- TABLE 2: user_analytics_ledger (MDS Score Tracking)
-- =====================================================================
-- Append-only ledger of value delivered to users
-- Used for dashboard graphs and cumulative MDS

CREATE TABLE IF NOT EXISTS user_analytics_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Context
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    
    -- Metrics
    minutes_saved FLOAT NOT NULL,
    mds_points FLOAT NOT NULL,
    atoms_executed INT NOT NULL,
    atoms_succeeded INT NOT NULL,
    atoms_failed INT NOT NULL,
    
    -- Cost Tracking
    tokens_used INT DEFAULT 0,
    cost_usd FLOAT DEFAULT 0.0,
    
    -- Categorization
    skill_category TEXT,
    
    -- Timestamp
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_analytics_org_user ON user_analytics_ledger (org_id, workspace_id, user_id);
CREATE INDEX IF NOT EXISTS idx_analytics_created ON user_analytics_ledger (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_session ON user_analytics_ledger (session_id);


-- =====================================================================
-- TABLE 3: execution_sessions (High-Level Session Summary)
-- =====================================================================
-- Stores summary of each user request execution
-- Links to Phase 4 execution_results for detailed traces

CREATE TABLE IF NOT EXISTS execution_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Identity
    session_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT,
    
    -- Goal
    user_goal TEXT NOT NULL,
    total_atoms INT NOT NULL,
    
    -- Status
    final_status TEXT NOT NULL CHECK (final_status IN ('COMPLETED', 'FAILED', 'PARTIAL', 'BLOCKED')),
    
    -- Summary
    error_summary TEXT,
    success_count INT DEFAULT 0,
    failure_count INT DEFAULT 0,
    
    -- Timing
    start_time TIMESTAMPTZ DEFAULT NOW(),
    end_time TIMESTAMPTZ,
    duration_seconds FLOAT,
    
    -- Validation
    validated BOOLEAN DEFAULT FALSE,
    validation_details JSONB
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_org ON execution_sessions (org_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON execution_sessions (final_status);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON execution_sessions (start_time DESC);


-- =====================================================================
-- TABLE 4: smart_recommendations (AI-Generated Suggestions)
-- =====================================================================
-- Stores pattern-detected optimization suggestions
-- Three types: WORKFLOW_DISCOVERY, POLICY_UPDATE, PROMPT_OPTIMIZATION

CREATE TABLE IF NOT EXISTS smart_recommendations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Context
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    
    -- Recommendation Type
    rec_type TEXT NOT NULL CHECK (rec_type IN ('WORKFLOW_DISCOVERY', 'POLICY_UPDATE', 'PROMPT_OPTIMIZATION')),
    engine_source TEXT NOT NULL,
    
    -- Content
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    proposal_data JSONB NOT NULL,
    
    -- Evidence
    confidence FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    trigger_events JSONB NOT NULL,
    sample_count INT DEFAULT 0,
    
    -- Status
    status TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'APPLIED')),
    
    -- Timestamps
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ,
    reviewed_by TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_recommendations_org_status ON smart_recommendations (org_id, workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_recommendations_type ON smart_recommendations (rec_type);
CREATE INDEX IF NOT EXISTS idx_recommendations_generated ON smart_recommendations (generated_at DESC);


-- =====================================================================
-- TABLE 5: pattern_detection_cache (Learning State)
-- =====================================================================
-- Caches detected patterns to avoid recomputing
-- Used by Smart Recommender background job

CREATE TABLE IF NOT EXISTS pattern_detection_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Context
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    
    -- Pattern
    pattern_type TEXT NOT NULL,
    pattern_signature TEXT NOT NULL,
    occurrence_count INT NOT NULL,
    
    -- Evidence
    session_ids JSONB NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL,
    last_seen TIMESTAMPTZ NOT NULL,
    
    -- Status
    recommendation_generated BOOLEAN DEFAULT FALSE,
    recommendation_id UUID REFERENCES smart_recommendations(id),
    
    UNIQUE (org_id, workspace_id, pattern_signature)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_patterns_org ON pattern_detection_cache (org_id, workspace_id);
CREATE INDEX IF NOT EXISTS idx_patterns_signature ON pattern_detection_cache (pattern_signature);


-- =====================================================================
-- TABLE 6: user_feedback (User Corrections & Edits)
-- =====================================================================
-- Tracks when users edit AI outputs
-- Used for prompt optimization learning

CREATE TABLE IF NOT EXISTS user_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Context
    org_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    atom_id UUID NOT NULL,
    session_id TEXT NOT NULL,
    
    -- Feedback Type
    feedback_type TEXT NOT NULL CHECK (feedback_type IN ('EDIT', 'CORRECTION', 'RATING', 'COMMENT')),
    
    -- Content
    original_output JSONB,
    edited_output JSONB,
    diff_summary TEXT,
    user_rating INT CHECK (user_rating >= 1 AND user_rating <= 5),
    user_comment TEXT,
    
    -- Timestamp
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_feedback_org_user ON user_feedback (org_id, workspace_id, user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_atom ON user_feedback (atom_id);
CREATE INDEX IF NOT EXISTS idx_feedback_type ON user_feedback (feedback_type);


-- =====================================================================
-- MATERIALIZED VIEW: user_analytics_summary (Fast Dashboard Queries)
-- =====================================================================
-- Pre-aggregated analytics for quick dashboard loading

CREATE MATERIALIZED VIEW IF NOT EXISTS user_analytics_summary AS
SELECT 
    org_id,
    workspace_id,
    user_id,
    COUNT(DISTINCT session_id) AS total_sessions,
    SUM(minutes_saved) AS total_minutes_saved,
    SUM(mds_points) AS total_mds_points,
    SUM(atoms_executed) AS total_atoms_executed,
    SUM(atoms_succeeded) AS total_atoms_succeeded,
    SUM(atoms_failed) AS total_atoms_failed,
    AVG(minutes_saved) AS avg_minutes_per_session,
    SUM(cost_usd) AS total_cost_usd,
    MAX(created_at) AS last_activity
FROM user_analytics_ledger
GROUP BY org_id, workspace_id, user_id;

-- Index for fast lookups
CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_summary_user ON user_analytics_summary (org_id, workspace_id, user_id);


-- =====================================================================
-- RPC FUNCTION 1: log_analytics_event
-- =====================================================================
-- Records a value-delivered event in the ledger

CREATE OR REPLACE FUNCTION log_analytics_event(
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_user_id TEXT,
    p_session_id TEXT,
    p_request_id TEXT,
    p_minutes_saved FLOAT,
    p_mds_points FLOAT,
    p_atoms_executed INT,
    p_atoms_succeeded INT,
    p_atoms_failed INT,
    p_tokens_used INT DEFAULT 0,
    p_cost_usd FLOAT DEFAULT 0.0,
    p_skill_category TEXT DEFAULT NULL
) RETURNS UUID AS $$
DECLARE
    v_event_id UUID;
BEGIN
    INSERT INTO user_analytics_ledger (
        org_id, workspace_id, user_id, session_id, request_id,
        minutes_saved, mds_points, atoms_executed, atoms_succeeded, atoms_failed,
        tokens_used, cost_usd, skill_category
    ) VALUES (
        p_org_id, p_workspace_id, p_user_id, p_session_id, p_request_id,
        p_minutes_saved, p_mds_points, p_atoms_executed, p_atoms_succeeded, p_atoms_failed,
        p_tokens_used, p_cost_usd, p_skill_category
    )
    RETURNING id INTO v_event_id;
    
    RETURN v_event_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 2: create_recommendation
-- =====================================================================
-- Creates a smart recommendation

CREATE OR REPLACE FUNCTION create_recommendation(
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_rec_type TEXT,
    p_engine_source TEXT,
    p_title TEXT,
    p_description TEXT,
    p_proposal_data JSONB,
    p_confidence FLOAT,
    p_trigger_events JSONB,
    p_sample_count INT
) RETURNS UUID AS $$
DECLARE
    v_rec_id UUID;
BEGIN
    INSERT INTO smart_recommendations (
        org_id, workspace_id, rec_type, engine_source,
        title, description, proposal_data,
        confidence, trigger_events, sample_count
    ) VALUES (
        p_org_id, p_workspace_id, p_rec_type, p_engine_source,
        p_title, p_description, p_proposal_data,
        p_confidence, p_trigger_events, p_sample_count
    )
    RETURNING id INTO v_rec_id;
    
    RETURN v_rec_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 3: get_user_analytics
-- =====================================================================
-- Query user analytics summary

CREATE OR REPLACE FUNCTION get_user_analytics(
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_user_id TEXT
) RETURNS TABLE (
    total_sessions BIGINT,
    total_minutes_saved NUMERIC,
    total_mds_points NUMERIC,
    total_atoms_executed NUMERIC,
    total_atoms_succeeded NUMERIC,
    total_atoms_failed NUMERIC,
    avg_minutes_per_session NUMERIC,
    total_cost_usd NUMERIC,
    last_activity TIMESTAMPTZ
) AS $$
BEGIN
    -- Refresh materialized view
    REFRESH MATERIALIZED VIEW CONCURRENTLY user_analytics_summary;
    
    RETURN QUERY
    SELECT 
        s.total_sessions,
        s.total_minutes_saved,
        s.total_mds_points,
        s.total_atoms_executed,
        s.total_atoms_succeeded,
        s.total_atoms_failed,
        s.avg_minutes_per_session,
        s.total_cost_usd,
        s.last_activity
    FROM user_analytics_summary s
    WHERE s.org_id = p_org_id
      AND s.workspace_id = p_workspace_id
      AND s.user_id = p_user_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 4: get_pending_recommendations
-- =====================================================================
-- Fetch pending recommendations for review

CREATE OR REPLACE FUNCTION get_pending_recommendations(
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_limit INT DEFAULT 10
) RETURNS TABLE (
    id UUID,
    rec_type TEXT,
    title TEXT,
    description TEXT,
    proposal_data JSONB,
    confidence FLOAT,
    sample_count INT,
    generated_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        r.id, r.rec_type, r.title, r.description,
        r.proposal_data, r.confidence, r.sample_count, r.generated_at
    FROM smart_recommendations r
    WHERE r.org_id = p_org_id
      AND r.workspace_id = p_workspace_id
      AND r.status = 'PENDING'
    ORDER BY r.confidence DESC, r.generated_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 5: apply_recommendation
-- =====================================================================
-- Mark recommendation as applied

CREATE OR REPLACE FUNCTION apply_recommendation(
    p_rec_id UUID,
    p_reviewed_by TEXT
) RETURNS BOOLEAN AS $$
BEGIN
    UPDATE smart_recommendations
    SET status = 'APPLIED',
        reviewed_at = NOW(),
        reviewed_by = p_reviewed_by
    WHERE id = p_rec_id
      AND status = 'PENDING';
    
    RETURN FOUND;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- RPC FUNCTION 6: record_session_summary
-- =====================================================================
-- Create execution session summary

CREATE OR REPLACE FUNCTION record_session_summary(
    p_session_id TEXT,
    p_request_id TEXT,
    p_org_id TEXT,
    p_workspace_id TEXT,
    p_user_id TEXT,
    p_user_goal TEXT,
    p_total_atoms INT,
    p_final_status TEXT,
    p_error_summary TEXT DEFAULT NULL,
    p_success_count INT DEFAULT 0,
    p_failure_count INT DEFAULT 0,
    p_duration_seconds FLOAT DEFAULT 0.0
) RETURNS UUID AS $$
DECLARE
    v_session_rec_id UUID;
BEGIN
    INSERT INTO execution_sessions (
        session_id, request_id, org_id, workspace_id, user_id,
        user_goal, total_atoms, final_status,
        error_summary, success_count, failure_count,
        end_time, duration_seconds
    ) VALUES (
        p_session_id, p_request_id, p_org_id, p_workspace_id, p_user_id,
        p_user_goal, p_total_atoms, p_final_status,
        p_error_summary, p_success_count, p_failure_count,
        NOW(), p_duration_seconds
    )
    RETURNING id INTO v_session_rec_id;
    
    RETURN v_session_rec_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- =====================================================================
-- Enable RLS on all tables
-- =====================================================================

ALTER TABLE user_analytics_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE execution_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE smart_recommendations ENABLE ROW LEVEL SECURITY;
ALTER TABLE pattern_detection_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_feedback ENABLE ROW LEVEL SECURITY;

-- RLS Policies (org isolation)
CREATE POLICY analytics_org_isolation ON user_analytics_ledger
    FOR ALL USING (org_id = current_setting('app.org_id', true));

CREATE POLICY sessions_org_isolation ON execution_sessions
    FOR ALL USING (org_id = current_setting('app.org_id', true));

CREATE POLICY recommendations_org_isolation ON smart_recommendations
    FOR ALL USING (org_id = current_setting('app.org_id', true));

CREATE POLICY patterns_org_isolation ON pattern_detection_cache
    FOR ALL USING (org_id = current_setting('app.org_id', true));

CREATE POLICY feedback_org_isolation ON user_feedback
    FOR ALL USING (org_id = current_setting('app.org_id', true));


-- =====================================================================
-- END OF MIGRATION
-- =====================================================================
