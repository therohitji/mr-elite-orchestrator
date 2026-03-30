"""Events Repository: Append-only event log operations

Handles reading and writing to workflow_state_events table.
Responsible for deterministic replay and audit trail.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


class EventsRepository:
    """Repository for immutable workflow event log."""

    def __init__(self, db_client):
        """Initialize with database client.

        Args:
            db_client: Supabase client instance
        """
        self.db = db_client

    async def append_event(
        self,
        session_id: UUID,
        workspace_id: UUID,
        org_id: UUID,
        user_id: Optional[UUID],
        event_type: str,
        node: Optional[str],
        idempotency_key: Optional[str],
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Append event to log using atomic RPC.

        This uses the database RPC function for atomic sequence assignment.
        Prevents duplicate events via idempotency_key (if provided).

        Args:
            session_id: Session ID
            workspace_id: Workspace ID
            org_id: Organization ID
            user_id: User who triggered event
            event_type: Event type (tool_called, tool_result, approval_requested, etc.)
            node: Node where event occurred
            idempotency_key: Optional dedup key (for retries)
            payload: Event data (tool_name, result, error, etc.)

        Returns:
            Event dict with event_id and sequence, or None if failed
        """
        try:
            response = self.db.rpc(
                "append_workflow_event",
                {
                    "p_session_id": str(session_id),
                    "p_workspace_id": str(workspace_id),
                    "p_user_id": str(user_id) if user_id else None,
                    "p_org_id": str(org_id),
                    "p_event_type": event_type,
                    "p_node": node,
                    "p_idempotency_key": idempotency_key,
                    "p_payload": payload,
                },
            ).execute()

            if response.data and len(response.data) > 0:
                event_info = response.data[0]
                logger.debug(
                    f"✅ Event {event_type} appended to {session_id} at seq {event_info['sequence']}"
                )
                return event_info
            return None

        except Exception as e:
            logger.error(f"❌ Failed to append event to {session_id}: {e}")
            return None

    async def get_events_since(
        self,
        session_id: UUID,
        last_sequence: int = 0,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Get events for delta replay (after last_sequence).

        Used during resume to hydrate state with events since last checkpoint.
        Events are returned in strict sequence order.

        Args:
            session_id: Session to query
            last_sequence: Start after this sequence (0 = all events)
            limit: Max events to return

        Returns:
            List of event dicts in sequence order
        """
        try:
            response = self.db.rpc(
                "get_events_since",
                {
                    "p_session_id": str(session_id),
                    "p_last_sequence": last_sequence,
                    "p_limit": limit,
                },
            ).execute()

            logger.debug(
                f"✅ Retrieved {len(response.data or [])} events for {session_id}"
            )
            return response.data or []

        except Exception as e:
            logger.error(f"❌ Failed to get events for {session_id}: {e}")
            return []

    async def get_event_timeline(
        self,
        session_id: UUID,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get full event timeline for a session (for history replay).

        Args:
            session_id: Session to query
            limit: Max events to return

        Returns:
            List of all events in sequence order
        """
        try:
            response = (
                self.db.table("workflow_state_events")
                .select("*")
                .eq("session_id", str(session_id))
                .order("sequence", desc=False)
                .limit(limit)
                .execute()
            )

            return response.data or []

        except Exception as e:
            logger.error(f"❌ Failed to get timeline for {session_id}: {e}")
            return []

    async def get_events_by_type(
        self,
        session_id: UUID,
        event_type: str,
    ) -> List[Dict[str, Any]]:
        """Get all events of specific type (for analysis).

        Args:
            session_id: Session to query
            event_type: Filter by event type

        Returns:
            List of events of that type
        """
        try:
            response = (
                self.db.table("workflow_state_events")
                .select("*")
                .eq("session_id", str(session_id))
                .eq("event_type", event_type)
                .order("sequence")
                .execute()
            )

            return response.data or []

        except Exception as e:
            logger.error(f"❌ Failed to get {event_type} events for {session_id}: {e}")
            return []

    async def get_tool_calls_for_session(
        self, session_id: UUID
    ) -> List[Dict[str, Any]]:
        """Get all tool_called events (for reconciliation).

        Args:
            session_id: Session to query

        Returns:
            List of tool_called events with payload
        """
        return await self.get_events_by_type(session_id, "tool_called")

    async def get_tool_results_for_session(
        self, session_id: UUID
    ) -> List[Dict[str, Any]]:
        """Get all tool_result events (for reconciliation).

        Args:
            session_id: Session to query

        Returns:
            List of tool_result events with outcomes
        """
        return await self.get_events_by_type(session_id, "tool_result")

    async def find_orphaned_tool_calls(self, session_id: UUID) -> List[Dict[str, Any]]:
        """Find tool_called events without matching tool_result (crash recovery).

        Used during resume to detect tools that were executed but not confirmed.
        Returns orphaned tool_called events that may need reconciliation.

        Args:
            session_id: Session to check

        Returns:
            List of orphaned tool_called events
        """
        try:
            tool_calls = await self.get_tool_calls_for_session(session_id)
            tool_results = await self.get_tool_results_for_session(session_id)

            # Create set of tool call IDs that have results
            result_call_ids = {
                result["payload"].get("tool_call_id")
                for result in tool_results
                if result.get("payload", {}).get("tool_call_id")
            }

            # Find orphaned calls
            orphaned = [
                call
                for call in tool_calls
                if call["payload"].get("tool_call_id") not in result_call_ids
            ]

            if orphaned:
                logger.warning(
                    f"⚠️ Found {len(orphaned)} orphaned tool calls for {session_id}"
                )
            return orphaned

        except Exception as e:
            logger.error(f"❌ Failed to find orphaned calls for {session_id}: {e}")
            return []

    async def record_approval_requested(
        self,
        session_id: UUID,
        workspace_id: UUID,
        org_id: UUID,
        user_id: Optional[UUID],
        approval_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Record approval_requested event.

        Args:
            session_id: Session requesting approval
            workspace_id: Workspace ID
            org_id: Org ID
            user_id: User requesting approval
            approval_context: What's being approved

        Returns:
            Event dict or None
        """
        return await self.append_event(
            session_id=session_id,
            workspace_id=workspace_id,
            org_id=org_id,
            user_id=user_id,
            event_type="approval_requested",
            node="human_approval",
            idempotency_key=None,
            payload=approval_context,
        )

    async def record_approval_decision(
        self,
        session_id: UUID,
        workspace_id: UUID,
        org_id: UUID,
        user_id: Optional[UUID],
        decision: str,  # 'approved' | 'denied'
        notes: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Record approval_decision event.

        Args:
            session_id: Session being approved/denied
            workspace_id: Workspace ID
            org_id: Org ID
            user_id: User making decision
            decision: 'approved' or 'denied'
            notes: Optional notes

        Returns:
            Event dict or None
        """
        return await self.append_event(
            session_id=session_id,
            workspace_id=workspace_id,
            org_id=org_id,
            user_id=user_id,
            event_type="approval_decision",
            node="human_approval",
            idempotency_key=None,
            payload={"decision": decision, "notes": notes},
        )

    async def record_node_entered(
        self,
        session_id: UUID,
        workspace_id: UUID,
        org_id: UUID,
        user_id: Optional[UUID],
        node: str,
    ) -> Optional[Dict[str, Any]]:
        """Record node_entered event.

        Args:
            session_id: Session ID
            workspace_id: Workspace ID
            org_id: Org ID
            user_id: User ID
            node: Node name

        Returns:
            Event dict or None
        """
        return await self.append_event(
            session_id=session_id,
            workspace_id=workspace_id,
            org_id=org_id,
            user_id=user_id,
            event_type="node_entered",
            node=node,
            idempotency_key=f"node_entered_{node}",
            payload={"node": node, "timestamp": datetime.utcnow().isoformat()},
        )

    async def record_error(
        self,
        session_id: UUID,
        workspace_id: UUID,
        org_id: UUID,
        user_id: Optional[UUID],
        node: Optional[str],
        error_message: str,
        error_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Record error event.

        Args:
            session_id: Session ID
            workspace_id: Workspace ID
            org_id: Org ID
            user_id: User ID
            node: Node where error occurred
            error_message: Error message
            error_type: Error category

        Returns:
            Event dict or None
        """
        return await self.append_event(
            session_id=session_id,
            workspace_id=workspace_id,
            org_id=org_id,
            user_id=user_id,
            event_type="error",
            node=node,
            idempotency_key=None,
            payload={
                "message": error_message,
                "type": error_type,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
