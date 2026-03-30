# app/core/session/__init__.py
"""
Session Module: Persistent workflow session management.

This module provides:
- SessionState: Complete session state schema
- SessionService: Service for session lifecycle management
- ClarificationEntry: Single clarification within a session
- ApprovalEntry: Approval tracking within a session

Usage:
    from app.core.session import SessionService, SessionState, get_session_service
    
    # Create service
    service = get_session_service(org_id="org123", workspace_id="ws456")
    
    # Resolve or create session
    resolution = await service.resolve_session(
        thread_id="thread_abc",
        session_id=None,  # or existing session_id
        clarification_id=None,  # or existing clarification_id
        message="Send an email to John"
    )
    
    # Access session
    session = resolution.session
    is_new = resolution.is_new
    
    # Add clarification
    entry = await service.add_clarification(
        session=session,
        atom_id="atom_123",
        domain="email",
        missing_fields=["recipient", "subject"],
        prompt="Who should I send this email to?"
    )
    
    # Update clarification with user response
    result = await service.update_clarification(
        session=session,
        clarification_id=entry.clarification_id,
        collected_field="recipient",
        collected_value="john@example.com",
        user_message="Send it to john@example.com",
        assistant_response="Got it, sending to john@example.com"
    )
    
    # Save checkpoint
    await service.save_checkpoint(
        session=session,
        phase=2,
        current_node="clarification_wait",
        phase_result={"atoms": [...]},
    )
"""

from app.core.session.state_schema import (
    # Enums
    SessionStatus,
    ClarificationStatus,
    ApprovalTrackingStatus,
    
    # State models
    SessionState,
    ClarificationEntry,
    ClarificationState,
    ApprovalEntry,
    ApprovalState,
    CheckpointSnapshot,
    
    # Result types
    SessionResolution,
    ClarificationResult,
)

from app.core.session.session_service import (
    SessionService,
    get_session_service,
    SESSION_TTL_HOURS,
    CLARIFICATION_TTL_MINUTES,
)

__all__ = [
    # Enums
    "SessionStatus",
    "ClarificationStatus",
    "ApprovalTrackingStatus",
    
    # State models
    "SessionState",
    "ClarificationEntry",
    "ClarificationState",
    "ApprovalEntry",
    "ApprovalState",
    "CheckpointSnapshot",
    
    # Result types
    "SessionResolution",
    "ClarificationResult",
    
    # Service
    "SessionService",
    "get_session_service",
    
    # Constants
    "SESSION_TTL_HOURS",
    "CLARIFICATION_TTL_MINUTES",
]
