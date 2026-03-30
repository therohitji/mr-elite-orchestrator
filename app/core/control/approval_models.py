# app/core/control/approval_models.py
"""
Approval Models

Defines schemas for approval workflow including pending approvals and decisions.
"""

from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Literal
from datetime import datetime


# ═══════════════════════════════════════════════════════════════
# APPROVAL REQUEST (Pending approval in database)
# ═══════════════════════════════════════════════════════════════

class ApprovalRequest(BaseModel):
    """
    Represents a pending approval stored in the database.
    
    Created when Phase 3 determines an atom requires approval.
    """
    approval_id: str
    atom_id: str
    atom_snapshot: dict  # Full atom data at time of approval request
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    requested_at: datetime
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    comment: Optional[str] = None
    
    # Extended fields for Phase 4 output
    skill: Optional[str] = None  # The skill being approved (e.g., "send_email")
    skill_category: Optional[str] = None  # Category (e.g., "communication")
    action_description: Optional[str] = None  # Human-readable action description
    reason: Optional[str] = None  # Why approval is required
    risk_level: Optional[str] = "MEDIUM"  # LOW, MEDIUM, HIGH
    
    # Display data for UI preview
    display: Optional[Dict[str, Any]] = None


# ═══════════════════════════════════════════════════════════════
# APPROVAL DECISION (Response from /approve endpoint)
# ═══════════════════════════════════════════════════════════════

class ApprovalDecisionOutput(BaseModel):
    """
    Output from approval decision endpoint.
    
    Returned by POST /approve when an approval is processed.
    """
    decision: Literal["APPROVED", "REJECTED"]
    approval_id: str
    atom_id: str
    skill: Optional[str] = None
    
    # For APPROVED: What happens next
    next_phase: Optional[str] = None  # e.g., "PHASE_4_EXECUTION"
    
    # For REJECTED: Reason
    reason: Optional[str] = None
    
    # Display data (for emails, shows what was approved)
    output: Optional[Dict[str, Any]] = None
    
    # Human-readable message
    message: Optional[str] = None

