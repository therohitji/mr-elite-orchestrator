"""State Reducer: Deterministic state evolution

Applies events to state snapshot in strict sequence order.
Used during resume to hydrate runtime state from event log.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class StateReducer:
    """Applies events deterministically to state."""

    @staticmethod
    def apply_event(state: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        """Apply single event to state (pure function).

        Deterministic: Same state + same events = same result.
        Used to hydrate runtime state from event log during resume.

        Args:
            state: Current state snapshot
            event: Event to apply

        Returns:
            Updated state
        """
        event_type = event.get("event_type")
        payload = event.get("payload", {})

        if event_type == "node_entered":
            state["current_node"] = payload.get("node")
            state["last_node"] = state.get("current_node")

        elif event_type == "tool_called":
            # Record tool execution intent
            if "tool_calls" not in state:
                state["tool_calls"] = []
            state["tool_calls"].append(
                {
                    "tool_call_id": payload.get("tool_call_id"),
                    "tool_name": payload.get("tool_name"),
                    "input": payload.get("input"),
                    "called_at": payload.get("called_at"),
                }
            )

        elif event_type == "tool_result":
            # Record tool execution result
            tool_call_id = payload.get("tool_call_id")
            if "tool_results" not in state:
                state["tool_results"] = {}
            state["tool_results"][tool_call_id] = {
                "result": payload.get("result"),
                "status": payload.get("status"),
                "external_id": payload.get("external_id"),
                "completed_at": payload.get("completed_at"),
            }

        elif event_type == "tool_failed":
            # Record tool failure
            tool_call_id = payload.get("tool_call_id")
            if "tool_failures" not in state:
                state["tool_failures"] = {}
            state["tool_failures"][tool_call_id] = {
                "error": payload.get("error"),
                "error_type": payload.get("error_type"),
                "failed_at": payload.get("failed_at"),
            }

        elif event_type == "approval_requested":
            # Session paused for approval
            state["approval_context"] = payload

        elif event_type == "approval_decision":
            # Approval decision made
            if "approval_context" in state:
                state["approval_context"]["decision"] = payload.get("decision")
                state["approval_context"]["decided_at"] = payload.get("decided_at")

        elif event_type == "user_message":
            # User provided input
            if "user_messages" not in state:
                state["user_messages"] = []
            state["user_messages"].append(
                {
                    "content": payload.get("content"),
                    "timestamp": payload.get("timestamp"),
                }
            )

        elif event_type == "ai_message":
            # AI generated message
            if "ai_messages" not in state:
                state["ai_messages"] = []
            state["ai_messages"].append(
                {
                    "content": payload.get("content"),
                    "timestamp": payload.get("timestamp"),
                }
            )

        elif event_type == "checkpoint":
            # Explicit checkpoint event
            if "checkpoints" not in state:
                state["checkpoints"] = []
            state["checkpoints"].append(
                {
                    "node": payload.get("node"),
                    "timestamp": payload.get("timestamp"),
                }
            )

        elif event_type == "error":
            # Error occurred
            state["last_error"] = {
                "message": payload.get("message"),
                "type": payload.get("type"),
                "timestamp": payload.get("timestamp"),
            }

        elif event_type == "state_updated":
            # Generic state update (merge payload into state)
            if "data" in payload:
                state.update(payload["data"])

        return state

    @staticmethod
    def apply_events(
        state: Dict[str, Any], events: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Apply multiple events in sequence (deterministic replay).

        Applies events in strict order to hydrate state.
        All events with same state should produce identical result.

        Args:
            state: Initial state
            events: List of events in sequence order

        Returns:
            Final state after all events applied
        """
        for event in events:
            state = StateReducer.apply_event(state, event)
        return state

    @staticmethod
    def validate_event_sequence(events: List[Dict[str, Any]]) -> bool:
        """Validate events are in strict sequence order.

        Args:
            events: Events to validate

        Returns:
            True if valid sequence, False otherwise
        """
        expected_seq = 1
        for event in events:
            seq = event.get("sequence")
            if seq != expected_seq:
                logger.error(f"❌ Sequence gap: expected {expected_seq}, got {seq}")
                return False
            expected_seq += 1
        return True

    @staticmethod
    def find_last_tool_call(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find the most recent unresolved tool call.

        Used to detect orphaned tools (called but never returned).

        Args:
            state: State snapshot

        Returns:
            Last tool call or None
        """
        tool_calls = state.get("tool_calls", [])
        tool_results = state.get("tool_results", {})

        for call in reversed(tool_calls):
            call_id = call.get("tool_call_id")
            if call_id not in tool_results:
                return call
        return None

    @staticmethod
    def get_pending_approvals(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get current pending approval if any.

        Args:
            state: State snapshot

        Returns:
            Approval context or None
        """
        approval = state.get("approval_context")
        if approval and not approval.get("decision"):
            return approval
        return None

    @staticmethod
    def get_execution_summary(state: Dict[str, Any]) -> Dict[str, Any]:
        """Get summary of execution so far.

        Args:
            state: State snapshot

        Returns:
            Summary dict with counts and status
        """
        tool_calls = state.get("tool_calls", [])
        tool_results = state.get("tool_results", {})
        tool_failures = state.get("tool_failures", {})

        return {
            "tools_called": len(tool_calls),
            "tools_succeeded": len(tool_results),
            "tools_failed": len(tool_failures),
            "current_node": state.get("current_node"),
            "has_pending_approval": StateReducer.get_pending_approvals(state)
            is not None,
            "has_error": "last_error" in state,
        }
