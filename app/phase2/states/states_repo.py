"""States Repository: Core snapshot operations

Handles CRUD operations on workflow_states table.
Responsible for storing and retrieving workflow snapshots.
"""

from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


class StatesRepository:
    """Repository for workflow session snapshots."""

    def __init__(self, db_client):
        """Initialize with database client (Supabase).

        Args:
            db_client: Supabase client instance
        """
        self.db = db_client

    async def create_session(
        self,
        org_id: UUID,
        workspace_id: UUID,
        user_id: Optional[UUID],
        title: Optional[str],
        initial_node: str,
        initial_state_data: Dict[str, Any],
        priority: str = "medium",
    ) -> UUID:
        """Create a new workflow session.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            user_id: User who started the session
            title: Human-readable session title
            initial_node: Starting node name (e.g., 'planner', 'executor')
            initial_state_data: Initial brain snapshot (messages, variables, plan)
            priority: Session priority (low|medium|high)

        Returns:
            session_id (UUID) of created session

        Raises:
            Exception: If database insert fails
        """
        try:
            response = (
                self.db.table("workflow_states")
                .insert(
                    {
                        "org_id": str(org_id),
                        "workspace_id": str(workspace_id),
                        "user_id": str(user_id) if user_id else None,
                        "title": title,
                        "status": "running",
                        "current_node": initial_node,
                        "priority": priority,
                        "state_data": initial_state_data,
                        "version": 1,
                        "last_event_seq": 0,
                    }
                )
                .execute()
            )

            session_id = UUID(response.data[0]["session_id"])
            logger.info(f"✅ Created session {session_id} at node '{initial_node}'")
            return session_id

        except Exception as e:
            logger.error(f"❌ Failed to create session: {e}")
            raise

    async def get_session(self, session_id: UUID) -> Optional[Dict[str, Any]]:
        """Fetch session snapshot.

        Args:
            session_id: Session ID to fetch

        Returns:
            Session dict or None if not found
        """
        try:
            response = (
                self.db.table("workflow_states")
                .select("*")
                .eq("session_id", str(session_id))
                .execute()
            )

            if response.data:
                return response.data[0]
            return None

        except Exception as e:
            logger.error(f"❌ Failed to get session {session_id}: {e}")
            return None

    async def checkpoint_snapshot(
        self,
        session_id: UUID,
        current_node: str,
        state_data: Dict[str, Any],
        status: str = "running",
        last_event_seq: int = 0,
    ) -> bool:
        """Update session snapshot (checkpoint).

        Creates or updates the snapshot after state changes.
        Always updates last_event_seq to prevent stale data.

        Args:
            session_id: Session to update
            current_node: Current node name
            state_data: Full state data (messages, variables, plan)
            status: Session status (running|paused|completed|failed|cancelled)
            last_event_seq: Sequence of last applied event

        Returns:
            True if successful, False otherwise
        """
        try:
            response = (
                self.db.table("workflow_states")
                .update(
                    {
                        "current_node": current_node,
                        "state_data": state_data,
                        "status": status,
                        "last_event_seq": last_event_seq,
                        "last_event_at": datetime.utcnow().isoformat(),
                    }
                )
                .eq("session_id", str(session_id))
                .execute()
            )

            if response.data:
                logger.debug(
                    f"✅ Checkpointed session {session_id} at '{current_node}'"
                )
                return True
            return False

        except Exception as e:
            logger.error(f"❌ Failed to checkpoint {session_id}: {e}")
            return False

    async def pause_session(
        self,
        session_id: UUID,
        reason: Optional[str] = None,
        approval_payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Pause a session waiting for human approval.

        Sets status to 'paused' and current_node to 'human_approval'.
        Stores approval context in state_data.

        Args:
            session_id: Session to pause
            reason: Why session is paused (approval_required, policy_violation, etc.)
            approval_payload: Context data for approval decision

        Returns:
            True if paused successfully
        """
        try:
            session = await self.get_session(session_id)
            if not session:
                return False

            state_data = session["state_data"].copy()
            state_data["approval_context"] = {
                "reason": reason,
                "payload": approval_payload or {},
                "paused_at": datetime.utcnow().isoformat(),
            }

            return await self.checkpoint_snapshot(
                session_id,
                current_node="human_approval",
                state_data=state_data,
                status="paused",
            )

        except Exception as e:
            logger.error(f"❌ Failed to pause session {session_id}: {e}")
            return False

    async def resume_after_approval(
        self,
        session_id: UUID,
        decision: str,  # 'approved' | 'denied'
        notes: Optional[str] = None,
    ) -> bool:
        """Resume session after human approval decision.

        Moves session from 'paused' back to 'running'.
        Records approval decision in state_data.

        Args:
            session_id: Session to resume
            decision: 'approved' or 'denied'
            notes: Optional notes from approver

        Returns:
            True if resumed successfully
        """
        try:
            session = await self.get_session(session_id)
            if not session:
                return False

            state_data = session["state_data"].copy()
            if "approval_context" in state_data:
                state_data["approval_context"]["decision"] = decision
                state_data["approval_context"]["notes"] = notes
                state_data["approval_context"][
                    "decided_at"
                ] = datetime.utcnow().isoformat()

            # Move to next node after approval (depends on workflow)
            # This is a placeholder; actual node depends on phase 1 plan
            next_node = session.get("last_node", "executor")

            return await self.checkpoint_snapshot(
                session_id,
                current_node=next_node,
                state_data=state_data,
                status="running",
            )

        except Exception as e:
            logger.error(f"❌ Failed to resume approval for {session_id}: {e}")
            return False

    async def fail_session(
        self,
        session_id: UUID,
        error_message: str,
        error_type: str = "unknown",
        error_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Mark session as failed.

        Args:
            session_id: Session to fail
            error_message: Human-readable error message
            error_type: Error category (system_error, logic_error, etc.)
            error_context: Additional error data

        Returns:
            True if marked as failed
        """
        try:
            session = await self.get_session(session_id)
            if not session:
                return False

            state_data = session["state_data"].copy()
            state_data["error"] = {
                "message": error_message,
                "type": error_type,
                "context": error_context or {},
                "timestamp": datetime.utcnow().isoformat(),
            }

            return await self.checkpoint_snapshot(
                session_id,
                current_node="error_handler",
                state_data=state_data,
                status="failed",
            )

        except Exception as e:
            logger.error(f"❌ Failed to mark {session_id} as failed: {e}")
            return False

    async def complete_session(self, session_id: UUID) -> bool:
        """Mark session as completed.

        Args:
            session_id: Session to complete

        Returns:
            True if marked as completed
        """
        try:
            session = await self.get_session(session_id)
            if not session:
                return False

            return await self.checkpoint_snapshot(
                session_id,
                current_node="__end__",
                state_data=session["state_data"],
                status="completed",
            )

        except Exception as e:
            logger.error(f"❌ Failed to complete session {session_id}: {e}")
            return False

    async def list_sessions_by_workspace(
        self,
        workspace_id: UUID,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """List sessions for a workspace (for UI dashboard).

        Args:
            workspace_id: Workspace to list
            status: Filter by status (optional)
            limit: Max sessions to return

        Returns:
            List of session dicts
        """
        try:
            query = (
                self.db.table("workflow_states")
                .select("*")
                .eq("workspace_id", str(workspace_id))
            )

            if status:
                query = query.eq("status", status)

            response = query.order("updated_at", desc=True).limit(limit).execute()
            return response.data or []

        except Exception as e:
            logger.error(f"❌ Failed to list sessions for {workspace_id}: {e}")
            return []

    async def get_pending_approvals(self, workspace_id: UUID) -> list:
        """Get sessions waiting for human approval (for approval queue UI).

        Args:
            workspace_id: Workspace to check

        Returns:
            List of paused sessions at 'human_approval' node
        """
        try:
            response = (
                self.db.table("workflow_states")
                .select("*")
                .eq("workspace_id", str(workspace_id))
                .eq("status", "paused")
                .eq("current_node", "human_approval")
                .order("updated_at", desc=True)
                .execute()
            )

            return response.data or []

        except Exception as e:
            logger.error(f"❌ Failed to get approvals for {workspace_id}: {e}")
            return []
