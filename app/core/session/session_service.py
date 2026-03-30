# app/core/session/session_service.py
"""
Session Service: Manages workflow session lifecycle with Supabase persistence.

This service provides:
1. Session resolution (find existing or create new)
2. Session state persistence
3. Clarification management within sessions
4. Approval tracking within sessions
5. Crash-safe resume capability

All state is persisted to workflow_states.state_data JSONB column.
"""

import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from uuid import uuid4, UUID

from app.core.supabase_client import supabase_client
from app.core.session.state_schema import (
    SessionState,
    SessionStatus,
    SessionResolution,
    ClarificationState,
    ClarificationEntry,
    ClarificationStatus,
    ClarificationResult,
    ApprovalState,
    ApprovalEntry,
    ApprovalTrackingStatus,
    CheckpointSnapshot,
)

logger = logging.getLogger(__name__)


def _is_valid_uuid(value: Optional[str]) -> bool:
    """Check if a string is a valid UUID format."""
    if not value:
        return False
    try:
        UUID(value)
        return True
    except (ValueError, TypeError):
        return False


# ─────────────────── CONFIGURATION ──────────────────

SESSION_TTL_HOURS = 24 * 7  # 7 days default TTL
CLARIFICATION_TTL_MINUTES = 60  # 1 hour for clarifications
MAX_SESSIONS_PER_THREAD = 100  # Limit sessions per thread


class SessionService:
    """
    Service for managing workflow sessions with Supabase persistence.
    
    Sessions are stored in workflow_states table with state_data JSONB
    containing the full SessionState schema.
    """
    
    def __init__(self, org_id: str, workspace_id: str):
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.db = supabase_client
    
    # ─────────────────── SESSION RESOLUTION ──────────────────
    
    async def resolve_session(
        self,
        thread_id: str,
        session_id: Optional[str] = None,
        clarification_id: Optional[str] = None,
        user_id: Optional[str] = None,
        message: str = "",
    ) -> SessionResolution:
        """
        Resolve or create a session for the given request.
        
        Resolution priority:
        1. If clarification_id provided → find session containing that clarification
        2. If session_id provided → find that specific session
        3. Otherwise → create new session for thread
        
        Args:
            thread_id: Thread identifier from frontend
            session_id: Optional session ID from frontend
            clarification_id: Optional clarification ID being responded to
            user_id: User making the request
            message: Original message for session title
            
        Returns:
            SessionResolution with session and creation/resume status
        """
        # Priority 1: Resolve by clarification_id
        if clarification_id:
            session = await self._find_session_by_clarification(clarification_id)
            if session:
                logger.info(f"[Session] Resolved by clarification_id: {session.session_id}")
                return SessionResolution(
                    session=session,
                    is_new=False,
                    is_resuming=True,
                )
        
        # Priority 2: Resolve by session_id
        if session_id:
            session = await self._get_session(session_id)
            if session:
                logger.info(f"[Session] Resolved by session_id: {session_id}")
                is_resuming = session.status == SessionStatus.PAUSED
                return SessionResolution(
                    session=session,
                    is_new=False,
                    is_resuming=is_resuming,
                    resume_from_phase=session.checkpoint.phase_reached if is_resuming else None,
                )
        
        # Priority 3: Create new session
        session = await self._create_session(
            thread_id=thread_id,
            user_id=user_id,
            message=message,
        )
        logger.info(f"[Session] Created new session: {session.session_id}")
        return SessionResolution(session=session, is_new=True)
    
    async def _find_session_by_clarification(
        self, clarification_id: str
    ) -> Optional[SessionState]:
        """Find session containing the given clarification_id."""
        try:
            # Query workflow_states where state_data contains the clarification_id
            # Using JSONB containment operator via raw query
            result = (
                self.db.table("workflow_states")
                .select("*")
                .eq("org_id", self.org_id)
                .eq("workspace_id", self.workspace_id)
                .execute()
            )
            
            if not result.data:
                return None
            
            # Search through results for matching clarification_id
            for record in result.data:
                state_data = record.get("state_data", {})
                clarifications = state_data.get("clarifications", {})
                entries = clarifications.get("entries", {})
                
                if clarification_id in entries:
                    return SessionState.from_db_dict(state_data)
            
            return None
            
        except Exception as e:
            logger.error(f"[Session] Failed to find session by clarification: {e}")
            return None
    
    async def load_session(self, session_id: str) -> Optional[SessionState]:
        """Public method to load session by ID."""
        return await self._get_session(session_id)

    async def _get_session(self, session_id: str) -> Optional[SessionState]:
        """Get session by session_id."""
        try:
            result = (
                self.db.table("workflow_states")
                .select("*")
                .eq("session_id", session_id)
                .single()
                .execute()
            )
            
            if not result.data:
                return None
            
            state_data = result.data.get("state_data", {})
            return SessionState.from_db_dict(state_data)
            
        except Exception as e:
            logger.warning(f"[Session] Failed to get session {session_id}: {e}")
            return None
    
    async def _create_session(
        self,
        thread_id: str,
        user_id: Optional[str] = None,
        message: str = "",
    ) -> SessionState:
        """Create a new session."""
        session_id = str(uuid4())
        now = datetime.utcnow()
        
        session = SessionState(
            session_id=session_id,
            thread_id=thread_id,
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            user_id=user_id,
            title=message[:100] if message else "New Session",
            status=SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
        )
        
        # Store original message in checkpoint
        session.checkpoint.original_message = message
        
        # Persist to database
        await self._persist_session(session, is_new=True)
        
        return session
    
    # ─────────────────── SESSION PERSISTENCE ──────────────────
    
    async def _persist_session(
        self, session: SessionState, is_new: bool = False
    ) -> bool:
        """Persist session state to database."""
        try:
            session.updated_at = datetime.utcnow()
            session.last_activity_at = datetime.utcnow()
            session.version += 1
            
            # Sanitize user_id (must be UUID)
            valid_user_id = session.user_id if _is_valid_uuid(session.user_id) else None
            
            db_record = {
                "session_id": session.session_id,
                "workspace_id": session.workspace_id,
                "org_id": session.org_id,
                "user_id": valid_user_id,
                "title": session.title,
                "status": session.status.value,
                "current_node": session.checkpoint.current_node,
                "last_node": session.checkpoint.last_node,
                "priority": session.priority,
                "state_data": session.to_db_dict(),
                "version": session.version,
                "last_event_seq": session.last_event_seq,
                "updated_at": session.updated_at.isoformat(),
                "last_event_at": session.last_activity_at.isoformat(),
            }
            
            logger.info(
                f"[Session] {'Creating' if is_new else 'Updating'} session {session.session_id} "
                f"(org={session.org_id}, workspace={session.workspace_id})"
            )
            
            if is_new:
                db_record["created_at"] = session.created_at.isoformat()
                result = (
                    self.db.table("workflow_states")
                    .insert(db_record)
                    .execute()
                )
            else:
                result = (
                    self.db.table("workflow_states")
                    .update(db_record)
                    .eq("session_id", session.session_id)
                    .execute()
                )
            
            if not result.data:
                logger.warning(f"[Session] Persist returned no data for {session.session_id}")
                return False
            
            logger.info(f"[Session] ✅ Persisted session {session.session_id} v{session.version}")
            return True
            
        except Exception as e:
            logger.error(f"[Session] ❌ Failed to persist session {session.session_id}: {e}", exc_info=True)
            return False

    
    async def save_session(self, session: SessionState) -> bool:
        """Public method to save session state."""
        return await self._persist_session(session, is_new=False)
    
    # ─────────────────── CLARIFICATION MANAGEMENT ──────────────────
    
    async def add_clarification(
        self,
        session: SessionState,
        atom_id: str,
        domain: str,
        missing_fields: List[str],
        prompt: str,
    ) -> ClarificationEntry:
        """
        Add a new clarification to the session.
        
        Args:
            session: Current session state
            atom_id: ID of atom requiring clarification
            domain: Domain (email, calendar, etc.)
            missing_fields: Fields that need to be collected
            prompt: Initial prompt to show user
            
        Returns:
            The created ClarificationEntry
        """
        clarification_id = str(uuid4())
        now = datetime.utcnow()
        
        entry = ClarificationEntry(
            clarification_id=clarification_id,
            atom_id=atom_id,
            domain=domain,
            missing_fields=missing_fields,
            status=ClarificationStatus.PENDING,
            created_at=now,
            updated_at=now,
            prompt=prompt,
        )
        
        # Add to session
        session.clarifications.entries[clarification_id] = entry
        session.clarifications.active_clarification_id = clarification_id
        session.clarifications.total_created += 1
        session.status = SessionStatus.PAUSED
        
        # Persist
        await self.save_session(session)
        
        logger.info(
            f"[Session] Added clarification {clarification_id} for atom {atom_id[:8]}..."
        )
        
        return entry
    
    async def update_clarification(
        self,
        session: SessionState,
        clarification_id: str,
        collected_field: str,
        collected_value: Any,
        user_message: str,
        assistant_response: str,
    ) -> ClarificationResult:
        """
        Update clarification with collected data.
        
        Args:
            session: Current session state
            clarification_id: ID of clarification to update
            collected_field: Field that was collected
            collected_value: Value collected from user
            user_message: Raw user message
            assistant_response: Assistant's response
            
        Returns:
            ClarificationResult with completion status
        """
        entry = session.clarifications.entries.get(clarification_id)
        if not entry:
            raise ValueError(f"Clarification {clarification_id} not found")
        
        # Update collected data
        entry.collected_data[collected_field] = collected_value
        entry.updated_at = datetime.utcnow()
        
        # Add to conversation history
        entry.conversation_history.append({"role": "user", "content": user_message})
        entry.conversation_history.append({"role": "assistant", "content": assistant_response})
        
        # Remove from missing fields
        if collected_field in entry.missing_fields:
            entry.missing_fields.remove(collected_field)
        
        # Check if complete
        is_complete = len(entry.missing_fields) == 0
        
        if is_complete:
            entry.status = ClarificationStatus.COMPLETE
            session.clarifications.total_completed += 1
            
            # Build enriched message
            enriched = self._build_enriched_message(session, entry)
            session.checkpoint.enriched_message = enriched
            
            # Check if all clarifications complete
            all_complete = all(
                e.status == ClarificationStatus.COMPLETE
                for e in session.clarifications.entries.values()
            )
            if all_complete:
                session.status = SessionStatus.ACTIVE
                session.clarifications.active_clarification_id = None
        else:
            entry.status = ClarificationStatus.PARTIAL
        
        # Persist
        await self.save_session(session)
        
        return ClarificationResult(
            clarification_id=clarification_id,
            is_complete=is_complete,
            collected_data=entry.collected_data,
            next_prompt=entry.prompt if not is_complete else None,
            enriched_message=session.checkpoint.enriched_message if is_complete else None,
        )
    
    def _build_enriched_message(
        self, session: SessionState, entry: ClarificationEntry
    ) -> str:
        """Build enriched message from clarification data."""
        original = session.checkpoint.original_message
        collected = entry.collected_data
        
        # Format collected data as context
        details = []
        for field, value in collected.items():
            details.append(f"- {field}: {value}")
        
        enriched = f"{original}\n\n[Clarification Details]\n" + "\n".join(details)
        return enriched
    
    async def get_active_clarification(
        self, session: SessionState
    ) -> Optional[ClarificationEntry]:
        """Get the currently active clarification, if any."""
        active_id = session.clarifications.active_clarification_id
        if not active_id:
            return None
        return session.clarifications.entries.get(active_id)
    
    # ─────────────────── APPROVAL TRACKING ──────────────────
    
    async def track_approval(
        self,
        session: SessionState,
        approval_id: str,
        atom_id: str,
        atom_snapshot: Optional[Dict[str, Any]] = None,
    ) -> ApprovalEntry:
        """
        Track an approval request in the session.
        
        The actual approval is stored in approval_queue table (legacy) or directly here.
        This tracks the relationship to the session.
        """
        entry = ApprovalEntry(
            approval_id=approval_id,
            atom_id=atom_id,
            status=ApprovalTrackingStatus.PENDING,
            created_at=datetime.utcnow(),
            atom_snapshot=atom_snapshot,
        )
        
        session.approvals.entries[approval_id] = entry
        session.approvals.pending_count += 1
        session.status = SessionStatus.PAUSED
        
        await self.save_session(session)
        
        logger.info(f"[Session] Tracking approval {approval_id} for atom {atom_id[:8]}...")
        
        return entry
    
    async def update_approval_status(
        self,
        session: SessionState,
        approval_id: str,
        status: ApprovalTrackingStatus,
        decided_by: Optional[str] = None,
    ) -> None:
        """Update approval status in session."""
        entry = session.approvals.entries.get(approval_id)
        if not entry:
            logger.warning(f"[Session] Approval {approval_id} not found in session")
            return
        
        old_status = entry.status
        entry.status = status
        entry.decided_at = datetime.utcnow()
        entry.decided_by = decided_by
        
        # Update counts
        if old_status == ApprovalTrackingStatus.PENDING:
            session.approvals.pending_count -= 1
        
        if status == ApprovalTrackingStatus.APPROVED:
            session.approvals.approved_count += 1
        elif status == ApprovalTrackingStatus.REJECTED:
            session.approvals.rejected_count += 1
        
        # Check if all approvals resolved and resume if so
        if session.approvals.pending_count == 0:
            # Only resume if no pending clarifications either
            has_pending_clarifications = any(
                e.status in (ClarificationStatus.PENDING, ClarificationStatus.PARTIAL)
                for e in session.clarifications.entries.values()
            )
            if not has_pending_clarifications:
                session.status = SessionStatus.ACTIVE
        
        await self.save_session(session)
    
    # ─────────────────── CHECKPOINT MANAGEMENT ──────────────────
    
    async def save_checkpoint(
        self,
        session: SessionState,
        phase: int,
        current_node: str,
        phase_result: Optional[Dict[str, Any]] = None,
        atoms_pending: Optional[List[Dict[str, Any]]] = None,
        atoms_executed: Optional[List[Dict[str, Any]]] = None,
        atom_outputs: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Save a checkpoint for crash-safe resume.
        
        Args:
            session: Current session
            phase: Phase number (1-5)
            current_node: Current execution node
            phase_result: Result from the phase (serialized)
            atoms_pending: Atoms still to execute
            atoms_executed: Atoms already executed
            atom_outputs: Outputs from executed atoms
        """
        checkpoint = session.checkpoint
        checkpoint.phase_reached = phase
        checkpoint.last_node = checkpoint.current_node
        checkpoint.current_node = current_node
        
        # Store phase result
        if phase_result:
            if phase == 1:
                checkpoint.phase1_result = phase_result
            elif phase == 2:
                checkpoint.phase2_result = phase_result
            elif phase == 3:
                checkpoint.phase3_result = phase_result
            elif phase == 4:
                checkpoint.phase4_result = phase_result
        
        # Update atom tracking
        if atoms_pending is not None:
            checkpoint.atoms_pending = atoms_pending
        if atoms_executed is not None:
            checkpoint.atoms_executed = atoms_executed
        if atom_outputs is not None:
            checkpoint.atom_outputs = atom_outputs
        
        session.last_event_seq += 1
        
        await self.save_session(session)
        
        logger.debug(f"[Session] Checkpoint saved at phase {phase}, node {current_node}")
    
    async def get_resumable_sessions(
        self, thread_id: Optional[str] = None
    ) -> List[SessionState]:
        """
        Get sessions that can be resumed.
        
        Returns sessions with status PAUSED that have valid checkpoints.
        """
        try:
            query = (
                self.db.table("workflow_states")
                .select("*")
                .eq("org_id", self.org_id)
                .eq("status", "paused")
            )
            
            if thread_id:
                # Filter by thread_id in state_data
                # Note: Full JSONB filtering may need raw SQL
                pass
            
            result = query.order("updated_at", desc=True).limit(50).execute()
            
            sessions = []
            for record in result.data or []:
                state_data = record.get("state_data", {})
                if thread_id:
                    # Filter by thread_id
                    if state_data.get("thread_id") != thread_id:
                        continue
                sessions.append(SessionState.from_db_dict(state_data))
            
            return sessions
            
        except Exception as e:
            logger.error(f"[Session] Failed to get resumable sessions: {e}")
            return []
    
    # ─────────────────── SESSION LIFECYCLE ──────────────────
    
    async def complete_session(self, session: SessionState) -> None:
        """Mark session as completed."""
        session.status = SessionStatus.COMPLETED
        await self.save_session(session)
        logger.info(f"[Session] Completed session {session.session_id}")
    
    async def fail_session(
        self, session: SessionState, error: str
    ) -> None:
        """Mark session as failed."""
        session.status = SessionStatus.FAILED
        # Store error in checkpoint for debugging
        session.checkpoint.phase4_result = session.checkpoint.phase4_result or {}
        session.checkpoint.phase4_result["error"] = error
        await self.save_session(session)
        logger.error(f"[Session] Failed session {session.session_id}: {error}")
    
    async def cancel_session(self, session: SessionState) -> None:
        """Mark session as cancelled."""
        session.status = SessionStatus.CANCELLED
        await self.save_session(session)
        logger.info(f"[Session] Cancelled session {session.session_id}")


# ─────────────────── MODULE-LEVEL HELPERS ──────────────────

def get_session_service(org_id: str, workspace_id: str) -> SessionService:
    """Factory function to create SessionService instance."""
    return SessionService(org_id=org_id, workspace_id=workspace_id)
