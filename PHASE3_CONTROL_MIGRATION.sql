-- ============================================================================
-- PHASE 3: CONTROL LAYER SCHEMA
-- ============================================================================
-- Tables: approval_queue, security_audit_logs, security_patterns_config
-- Purpose: Human approval gate + Security event logging + Dynamic security patterns
-- ============================================================================

-- ============================================================================
-- TABLE: approval_queue (Human Circuit Breaker / Yellow Light)
-- ============================================================================
-- Stores paused atoms awaiting human approval
-- ============================================================================

CREATE TABLE IF NOT EXISTS approval_queue (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  
  -- Multi-tenancy
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
  
  -- Atom linkage
  atom_payload JSONB NOT NULL,  -- Full frozen atom JSON
  atom_id TEXT NOT NULL,
  
  -- Risk assessment
  risk_level TEXT NOT NULL CHECK (risk_level IN ('HIGH', 'CRITICAL', 'MEDIUM')),
  risk_reason TEXT NOT NULL,
  
  -- Request metadata
  requested_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  agent_role TEXT,  -- e.g., "sales_bot", "hr_agent"
  
  -- Status tracking
  status TEXT NOT NULL DEFAULT 'PENDING' 
    CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELLED')),
  
  -- Approval tracking
  decided_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  decided_at TIMESTAMP,
  decision_comment TEXT,
  
  -- Security
  approval_token TEXT UNIQUE NOT NULL,  -- Hashed secret for API approval
  
  -- Expiration
  expires_at TIMESTAMP NOT NULL,
  
  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_approval_queue_org_status 
  ON approval_queue(org_id, status, created_at DESC);
  
CREATE INDEX idx_approval_queue_atom 
  ON approval_queue(atom_id);
  
CREATE INDEX idx_approval_queue_expires 
  ON approval_queue(expires_at) WHERE status = 'PENDING';

-- ============================================================================
-- TABLE: security_audit_logs (The Black Box)
-- ============================================================================
-- Records every security event (blocks, redactions, approvals)
-- ============================================================================

CREATE TABLE IF NOT EXISTS security_audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  
  -- Multi-tenancy
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
  
  -- Event classification
  event_type TEXT NOT NULL 
    CHECK (event_type IN ('PRIVACY_GUARD', 'SECURITY_GUARD', 'RELIABILITY_GUARD', 'APPROVAL_GATE', 'POLICY_VIOLATION')),
  severity TEXT NOT NULL 
    CHECK (severity IN ('INFO', 'WARNING', 'ERROR', 'CRITICAL')),
  
  -- Atom context
  atom_id TEXT,
  atom_payload JSONB,  -- Snapshot of atom that triggered event
  
  -- Detection details
  triggered_by TEXT,  -- Pattern name, rule ID, or guard name
  decision TEXT NOT NULL 
    CHECK (decision IN ('BLOCKED', 'REDACTED', 'APPROVED', 'PAUSED', 'FLAGGED')),
  reason TEXT NOT NULL,
  
  -- Source tracking
  agent_role TEXT,
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  
  -- Additional metadata
  metadata JSONB,  -- Extra context (e.g., pattern matched, policy violated)
  
  -- Timestamp
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_security_audit_org_type 
  ON security_audit_logs(org_id, event_type, created_at DESC);
  
CREATE INDEX idx_security_audit_severity 
  ON security_audit_logs(severity, created_at DESC);
  
CREATE INDEX idx_security_audit_atom 
  ON security_audit_logs(atom_id);

-- ============================================================================
-- TABLE: security_patterns_config (Dynamic Security Rules)
-- ============================================================================
-- Allows admins to add custom security patterns without code deployment
-- ============================================================================

CREATE TABLE IF NOT EXISTS security_patterns_config (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  
  -- Multi-tenancy
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  
  -- Pattern classification
  pattern_type TEXT NOT NULL 
    CHECK (pattern_type IN ('PII_REGEX', 'BLOCK_KEYWORD', 'SQL_RISK', 'INJECTION_PATTERN')),
  
  -- Pattern definition
  pattern_value TEXT NOT NULL,  -- Regex or keyword
  pattern_name TEXT NOT NULL,   -- Human-readable name
  
  -- Action on match
  action TEXT NOT NULL 
    CHECK (action IN ('REDACT', 'BLOCK', 'FLAG')),
  severity TEXT NOT NULL 
    CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
  
  -- Documentation
  description TEXT,
  
  -- Status
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  
  -- Timestamps
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by UUID REFERENCES auth.users(id) ON DELETE SET NULL
);

CREATE INDEX idx_security_patterns_org_type 
  ON security_patterns_config(org_id, pattern_type, enabled);

-- ============================================================================
-- RLS POLICIES (Row-Level Security)
-- ============================================================================

ALTER TABLE approval_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_patterns_config ENABLE ROW LEVEL SECURITY;

-- approval_queue policies
CREATE POLICY approval_queue_select ON approval_queue FOR SELECT
  USING (is_org_member(org_id));

CREATE POLICY approval_queue_insert ON approval_queue FOR INSERT
  WITH CHECK (is_org_member(org_id));

CREATE POLICY approval_queue_update ON approval_queue FOR UPDATE
  USING (is_org_member(org_id));

-- security_audit_logs policies (read-only for members, write by system)
CREATE POLICY security_audit_logs_select ON security_audit_logs FOR SELECT
  USING (is_org_member(org_id));

CREATE POLICY security_audit_logs_insert ON security_audit_logs FOR INSERT
  WITH CHECK (is_org_member(org_id));

-- security_patterns_config policies
CREATE POLICY security_patterns_config_select ON security_patterns_config FOR SELECT
  USING (is_org_member(org_id));

CREATE POLICY security_patterns_config_insert ON security_patterns_config FOR INSERT
  WITH CHECK (is_org_member(org_id));

CREATE POLICY security_patterns_config_update ON security_patterns_config FOR UPDATE
  USING (is_org_member(org_id));

-- ============================================================================
-- RPC FUNCTION: log_security_event (Audit Logging)
-- ============================================================================
-- Centralized security event logging
-- ============================================================================

CREATE OR REPLACE FUNCTION log_security_event(
  p_org_id UUID,
  p_workspace_id UUID,
  p_event_type TEXT,
  p_severity TEXT,
  p_atom_id TEXT,
  p_atom_payload JSONB,
  p_triggered_by TEXT,
  p_decision TEXT,
  p_reason TEXT,
  p_agent_role TEXT DEFAULT NULL,
  p_user_id UUID DEFAULT NULL,
  p_metadata JSONB DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
  v_log_id UUID;
BEGIN
  INSERT INTO security_audit_logs (
    org_id,
    workspace_id,
    event_type,
    severity,
    atom_id,
    atom_payload,
    triggered_by,
    decision,
    reason,
    agent_role,
    user_id,
    metadata
  ) VALUES (
    p_org_id,
    p_workspace_id,
    p_event_type,
    p_severity,
    p_atom_id,
    p_atom_payload,
    p_triggered_by,
    p_decision,
    p_reason,
    p_agent_role,
    p_user_id,
    p_metadata
  ) RETURNING id INTO v_log_id;
  
  RETURN v_log_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- RPC FUNCTION: create_approval_request (Approval Gate)
-- ============================================================================
-- Creates approval request and returns approval_token
-- ============================================================================

CREATE OR REPLACE FUNCTION create_approval_request(
  p_org_id UUID,
  p_workspace_id UUID,
  p_atom_payload JSONB,
  p_atom_id TEXT,
  p_risk_level TEXT,
  p_risk_reason TEXT,
  p_requested_by UUID DEFAULT NULL,
  p_agent_role TEXT DEFAULT NULL,
  p_expires_in_hours INT DEFAULT 24
)
RETURNS TABLE (
  approval_id UUID,
  approval_token TEXT,
  expires_at TIMESTAMP
) AS $$
DECLARE
  v_approval_id UUID;
  v_token TEXT;
  v_expires_at TIMESTAMP;
BEGIN
  -- Generate unique token
  v_token := encode(gen_random_bytes(32), 'hex');
  v_expires_at := CURRENT_TIMESTAMP + (p_expires_in_hours || ' hours')::INTERVAL;
  
  -- Insert approval request
  INSERT INTO approval_queue (
    org_id,
    workspace_id,
    atom_payload,
    atom_id,
    risk_level,
    risk_reason,
    requested_by,
    agent_role,
    approval_token,
    expires_at
  ) VALUES (
    p_org_id,
    p_workspace_id,
    p_atom_payload,
    p_atom_id,
    p_risk_level,
    p_risk_reason,
    p_requested_by,
    p_agent_role,
    v_token,
    v_expires_at
  ) RETURNING id INTO v_approval_id;
  
  RETURN QUERY SELECT v_approval_id, v_token, v_expires_at;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- RPC FUNCTION: approve_request (Approve Atom)
-- ============================================================================
-- Approves pending approval request
-- ============================================================================

CREATE OR REPLACE FUNCTION approve_request(
  p_approval_id UUID,
  p_approval_token TEXT,
  p_decided_by UUID,
  p_comment TEXT DEFAULT ''
)
RETURNS BOOLEAN AS $$
DECLARE
  v_token TEXT;
  v_status TEXT;
BEGIN
  -- Verify token and get status
  SELECT approval_token, status INTO v_token, v_status
  FROM approval_queue
  WHERE id = p_approval_id;
  
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Approval request not found';
  END IF;
  
  IF v_token != p_approval_token THEN
    RAISE EXCEPTION 'Invalid approval token';
  END IF;
  
  IF v_status != 'PENDING' THEN
    RAISE EXCEPTION 'Approval request already processed: %', v_status;
  END IF;
  
  -- Check expiration
  IF (SELECT expires_at FROM approval_queue WHERE id = p_approval_id) < CURRENT_TIMESTAMP THEN
    UPDATE approval_queue
    SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP
    WHERE id = p_approval_id;
    
    RAISE EXCEPTION 'Approval request expired';
  END IF;
  
  -- Approve
  UPDATE approval_queue
  SET 
    status = 'APPROVED',
    decided_by = p_decided_by,
    decided_at = CURRENT_TIMESTAMP,
    decision_comment = p_comment,
    updated_at = CURRENT_TIMESTAMP
  WHERE id = p_approval_id;
  
  RETURN TRUE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- RPC FUNCTION: reject_request (Reject Atom)
-- ============================================================================
-- Rejects pending approval request
-- ============================================================================

CREATE OR REPLACE FUNCTION reject_request(
  p_approval_id UUID,
  p_approval_token TEXT,
  p_decided_by UUID,
  p_comment TEXT DEFAULT ''
)
RETURNS BOOLEAN AS $$
DECLARE
  v_token TEXT;
  v_status TEXT;
BEGIN
  -- Verify token and get status
  SELECT approval_token, status INTO v_token, v_status
  FROM approval_queue
  WHERE id = p_approval_id;
  
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Approval request not found';
  END IF;
  
  IF v_token != p_approval_token THEN
    RAISE EXCEPTION 'Invalid approval token';
  END IF;
  
  IF v_status != 'PENDING' THEN
    RAISE EXCEPTION 'Approval request already processed: %', v_status;
  END IF;
  
  -- Reject
  UPDATE approval_queue
  SET 
    status = 'REJECTED',
    decided_by = p_decided_by,
    decided_at = CURRENT_TIMESTAMP,
    decision_comment = p_comment,
    updated_at = CURRENT_TIMESTAMP
  WHERE id = p_approval_id;
  
  RETURN TRUE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- TRIGGERS: Auto-update timestamps
-- ============================================================================

CREATE TRIGGER trg_approval_queue_updated 
  BEFORE UPDATE ON approval_queue
  FOR EACH ROW
  EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER trg_security_patterns_updated 
  BEFORE UPDATE ON security_patterns_config
  FOR EACH ROW
  EXECUTE FUNCTION update_timestamp();

-- ============================================================================
-- CRON JOB: Auto-expire old approvals (requires pg_cron extension)
-- ============================================================================

-- Run every hour to expire old pending approvals
-- SELECT cron.schedule(
--   'expire-old-approvals',
--   '0 * * * *',  -- Every hour
--   $$
--   UPDATE approval_queue
--   SET status = 'EXPIRED', updated_at = CURRENT_TIMESTAMP
--   WHERE status = 'PENDING' AND expires_at < CURRENT_TIMESTAMP;
--   $$
-- );

-- ============================================================================
-- SAMPLE DATA (for testing)
-- ============================================================================

INSERT INTO security_patterns_config (
  org_id, pattern_type, pattern_value, pattern_name, action, severity, description, enabled
) VALUES 
(
  (SELECT id FROM organizations LIMIT 1),
  'PII_REGEX',
  '\b(?!000|666)[0-9]{3}-(?!00)[0-9]{2}-(?!0000)[0-9]{4}\b',
  'SSN Detection',
  'REDACT',
  'HIGH',
  'Detects US Social Security Numbers (XXX-XX-XXXX format)',
  true
),
(
  (SELECT id FROM organizations LIMIT 1),
  'BLOCK_KEYWORD',
  'DROP TABLE',
  'SQL Injection - DROP TABLE',
  'BLOCK',
  'CRITICAL',
  'Blocks SQL injection attempts using DROP TABLE',
  true
),
(
  (SELECT id FROM organizations LIMIT 1),
  'INJECTION_PATTERN',
  'ignore previous instructions',
  'Prompt Injection',
  'BLOCK',
  'CRITICAL',
  'Blocks common prompt injection attacks',
  true
);

-- ============================================================================
-- END OF MIGRATION
-- ============================================================================
