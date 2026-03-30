# app/core/session/state_schema.py
"""
Session State Schema: Defines the structure of workflow session state.

This module contains Pydantic models and TypedDicts for the session state
stored in workflow_states.state_data JSONB column.

Session Semantics:
- One session per unique (thread_id, workflow_intent) pair
- Sessions persist across restarts via workflow_states table
- Sessions can contain multiple clarifications
- Sessions track approval state alongside execution state
"""

from typing import Optional, Dict, Any, List, Literal
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4
from pydantic import BaseModel, Field


# ─────────────────── SESSION STATUS ──────────────────

class SessionStatus(str, Enum):
    """Workflow session lifecycle status.
    
    NOTE: Values must match the workflow_status enum in Supabase:
    'running', 'paused', 'completed', 'failed', 'cancelled'
    """
    ACTIVE = "running"          # Session in progress (DB: 'running')
    PAUSED = "paused"           # Waiting for clarification or approval
    COMPLETED = "completed"     # All atoms executed successfully
    FAILED = "failed"           # Unrecoverable error
    CANCELLED = "cancelled"     # User or system cancelled



class ClarificationStatus(str, Enum):
    """Status of a single clarification request."""
    PENDING = "pending"         # Waiting for user response
    PARTIAL = "partial"         # Some fields collected, more needed
    COMPLETE = "complete"       # All fields collected
    EXPIRED = "expired"         # TTL exceeded


class ApprovalTrackingStatus(str, Enum):
    """Status of approval tracking within session."""
    PENDING = "pending"         # Waiting for approval decision
    APPROVED = "approved"       # Approved, ready for execution
    REJECTED = "rejected"       # Rejected by user


# ─────────────────── CLARIFICATION STATE ──────────────────

class ClarificationEntry(BaseModel):
    """
    A single clarification request within a session.
    
    Multiple clarifications can exist per session, each tracking
    a different atom that needs user input.
    """
    clarification_id: str = Field(default_factory=lambda: str(uuid4()))
    atom_id: str                           # Which atom this clarification is for
    domain: str                            # e.g., "email", "calendar", "general"
    missing_fields: List[str]              # Fields still needed
    collected_data: Dict[str, Any] = Field(default_factory=dict)  # Fields already collected
    conversation_history: List[Dict[str, str]] = Field(default_factory=list)
    status: ClarificationStatus = ClarificationStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    prompt: Optional[str] = None           # Current prompt shown to user


class ClarificationState(BaseModel):
    """
    Container for all clarifications in a session.
    Supports multiple concurrent clarifications.
    """
    entries: Dict[str, ClarificationEntry] = Field(default_factory=dict)  # keyed by clarification_id
    active_clarification_id: Optional[str] = None  # Currently active clarification
    total_created: int = 0
    total_completed: int = 0


# ─────────────────── APPROVAL STATE ──────────────────

class ApprovalEntry(BaseModel):
    """
    Tracking entry for an approval request.
    
    Note: The actual approval is stored in approval_queue table.
    This tracks the relationship to the session.
    """
    approval_id: str                       # References approval_queue.id
    atom_id: str                           # Which atom needs approval
    status: ApprovalTrackingStatus = ApprovalTrackingStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    decision_reason: Optional[str] = None  # Reason/comment for decision
    atom_snapshot: Optional[Dict[str, Any]] = None  # Snapshot of atom for execution


class ApprovalState(BaseModel):
    """
    Container for all approval tracking in a session.
    """
    entries: Dict[str, ApprovalEntry] = Field(default_factory=dict)  # keyed by approval_id
    pending_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0


# ─────────────────── CHECKPOINT STATE ──────────────────

class CheckpointSnapshot(BaseModel):
    """
    Snapshot of pipeline state for crash-safe resume.
    
    Captures enough information to resume execution from
    the last successful checkpoint after a restart.
    """
    phase_reached: int = 0                 # 1-5, last completed phase
    current_node: str = "entry"            # Current execution node
    last_node: Optional[str] = None        # Previous node for backtracking
    
    # Phase outputs (serialized for storage)
    phase1_result: Optional[Dict[str, Any]] = None
    phase2_result: Optional[Dict[str, Any]] = None
    phase3_result: Optional[Dict[str, Any]] = None
    phase4_result: Optional[Dict[str, Any]] = None
    
    # Atoms at various stages
    atoms_pending: List[Dict[str, Any]] = Field(default_factory=list)
    atoms_executed: List[Dict[str, Any]] = Field(default_factory=list)
    atoms_failed: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Execution outputs for dependency chaining
    atom_outputs: Dict[str, Any] = Field(default_factory=dict)
    
    # Resume context
    original_message: str = ""
    enriched_message: Optional[str] = None  # After clarifications
    context: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Memory ingestion idempotency (per spec: exactly-once episodic memory)
    memory_ingested: bool = False
    memory_ingested_at: Optional[datetime] = None
    workflow_memory_id: Optional[str] = None


# ─────────────────── SESSION STATE ──────────────────

class SessionState(BaseModel):
    """
    Complete session state stored in workflow_states.state_data.
    
    This is the root schema for all session-related data.
    Designed for persistence in JSONB and crash-safe resume.
    """
    # Identifiers
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    thread_id: str                         # From InputPayload
    org_id: str                            # Organization context
    workspace_id: str                      # Workspace context
    user_id: Optional[str] = None          # User who initiated
    
    # Session metadata
    title: Optional[str] = None            # Human-readable title
    status: SessionStatus = SessionStatus.ACTIVE
    priority: str = "medium"               # low, medium, high
    
    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None  # Session TTL
    
    # Clarification tracking
    clarifications: ClarificationState = Field(default_factory=ClarificationState)
    
    # Approval tracking
    approvals: ApprovalState = Field(default_factory=ApprovalState)
    
    # Checkpoint for resume
    checkpoint: CheckpointSnapshot = Field(default_factory=CheckpointSnapshot)
    
    # Concurrency control
    version: int = 1                       # Optimistic locking
    last_event_seq: int = 0                # Last event sequence number
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }
    
    def to_db_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database storage."""
        return self.model_dump(mode="json")
    
    @classmethod
    def from_db_dict(cls, data: Dict[str, Any]) -> "SessionState":
        """Reconstruct from database dictionary."""
        return cls.model_validate(data)


# ─────────────────── HELPER TYPES ──────────────────

@dataclass
class SessionResolution:
    """Result of resolving/creating a session."""
    session: SessionState
    is_new: bool
    is_resuming: bool = False              # True if resuming after restart
    resume_from_phase: Optional[int] = None


@dataclass
class ClarificationResult:
    """Result of processing a clarification response."""
    clarification_id: str
    is_complete: bool
    collected_data: Dict[str, Any]
    next_prompt: Optional[str] = None
    enriched_message: Optional[str] = None  # Set when complete
