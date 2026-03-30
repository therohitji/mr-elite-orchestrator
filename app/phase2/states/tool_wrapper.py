"""Tool Wrapper: Idempotent tool execution with event tracking

Wraps external tool calls to ensure crash-safety.
Records tool_called BEFORE execution, tool_result AFTER completion.
Supports idempotent retries and reconciliation.
"""

from typing import Dict, Any, Optional, Callable
from datetime import datetime
from uuid import uuid4, UUID
from enum import Enum
import logging
import hashlib

logger = logging.getLogger(__name__)


class ExecutionMode(str, Enum):
    """Tool execution modes."""

    EXECUTE = "execute"  # Call tool normally
    VERIFY = "verify"  # Check if tool was already called (reconciliation)
    SKIP = "skip"  # Don't call tool, use cached result


class ToolCallSummary:
    """Summary of a tool call execution."""

    def __init__(self, tool_name: str, tool_call_id: str, mode: ExecutionMode):
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.mode = mode
        self.executed_at: Optional[datetime] = None
        self.result: Optional[Any] = None
        self.external_id: Optional[str] = None
        self.error: Optional[str] = None
        self.success: bool = False


class ToolWrapper:
    """Wraps tool calls with crash-safety and idempotency."""

    def __init__(self, events_repo, states_repo):
        """Initialize with repositories.

        Args:
            events_repo: EventsRepository for recording events
            states_repo: StatesRepository for state updates
        """
        self.events_repo = events_repo
        self.states_repo = states_repo

    @staticmethod
    def compute_idempotency_key(
        tool_name: str,
        canonical_target: str,
        canonical_action: str,
    ) -> str:
        """Compute stable idempotency key for tool call.

        Formula: sha256(tool_name | target_id | action_type)

        Must be stable for same logical action.
        Never use timestamps (breaks deduplication).

        Args:
            tool_name: Name of tool
            canonical_target: What the tool operates on (e.g., user_id)
            canonical_action: What action to perform (send|approve|update)

        Returns:
            SHA256 hash of tool call
        """
        content = f"{tool_name}|{canonical_target}|{canonical_action}"
        return hashlib.sha256(content.encode()).hexdigest()

    async def execute_tool_safely(
        self,
        session_id: UUID,
        workspace_id: UUID,
        org_id: UUID,
        user_id: Optional[UUID],
        node: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_fn: Callable,
        idempotency_key: Optional[str] = None,
        mode: ExecutionMode = ExecutionMode.EXECUTE,
    ) -> ToolCallSummary:
        """Execute tool with crash-safety wrapper.

        Two-phase pattern:
        1. Record tool_called event (BEFORE calling tool)
        2. Execute tool
        3. Record tool_result or tool_failed event (AFTER execution)

        This ensures crash recovery can detect partially-executed tools.

        Args:
            session_id: Session ID
            workspace_id: Workspace ID
            org_id: Organization ID
            user_id: User ID
            node: Current node name
            tool_name: Name of tool to call
            tool_input: Input parameters
            tool_fn: Async callable that executes the tool
            idempotency_key: Optional dedup key (computed if not provided)
            mode: Execution mode (execute|verify|skip)

        Returns:
            ToolCallSummary with result
        """
        tool_call_id = str(uuid4())
        summary = ToolCallSummary(tool_name, tool_call_id, mode)

        try:
            # Compute idempotency key if not provided
            if not idempotency_key:
                canonical_target = tool_input.get(
                    "target_id", tool_input.get("id", "unknown")
                )
                canonical_action = tool_input.get("action", "default")
                idempotency_key = self.compute_idempotency_key(
                    tool_name, canonical_target, canonical_action
                )

            # Phase 1: Record that tool is about to be called
            logger.info(
                f"📝 Recording tool_called: {tool_name} (call_id={tool_call_id})"
            )

            call_event = await self.events_repo.append_event(
                session_id=session_id,
                workspace_id=workspace_id,
                org_id=org_id,
                user_id=user_id,
                event_type="tool_called",
                node=node,
                idempotency_key=idempotency_key,
                payload={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "input": tool_input,
                    "called_at": datetime.utcnow().isoformat(),
                },
            )

            if not call_event:
                logger.error(f"❌ Failed to record tool_called event")
                summary.error = "Failed to record tool call"
                return summary

            # Phase 2: Execute tool (or skip based on mode)
            if mode == ExecutionMode.SKIP:
                logger.warning(f"⏭️ Skipping execution: {tool_name} (cached)")
                summary.executed_at = datetime.utcnow()
                summary.result = {"status": "cached"}
                summary.success = True

            elif mode == ExecutionMode.VERIFY:
                logger.info(f"🔍 Verifying tool already executed: {tool_name}")
                # Don't call tool, just mark as verified
                summary.executed_at = datetime.utcnow()
                summary.result = {"status": "verified"}
                summary.success = True

            else:  # ExecutionMode.EXECUTE
                logger.info(f"⚙️ Executing tool: {tool_name}")
                summary.executed_at = datetime.utcnow()
                summary.result = await tool_fn(**tool_input)
                summary.success = True

            # Phase 3: Record tool result
            logger.info(f"✅ Recording tool_result: {tool_name}")

            result_event = await self.events_repo.append_event(
                session_id=session_id,
                workspace_id=workspace_id,
                org_id=org_id,
                user_id=user_id,
                event_type="tool_result",
                node=node,
                idempotency_key=None,  # Result events are not deduplicated
                payload={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "result": summary.result,
                    "status": "success",
                    "external_id": summary.external_id,
                    "completed_at": datetime.utcnow().isoformat(),
                },
            )

            if not result_event:
                logger.error(f"⚠️ Tool executed but failed to record result")
                # Tool already executed, but we couldn't record it
                # On resume, we'll detect this orphaned call
                summary.error = "Executed but result not recorded"
                return summary

            return summary

        except Exception as e:
            logger.error(f"❌ Tool execution failed: {tool_name}: {e}")
            summary.error = str(e)

            # Record failure event
            try:
                await self.events_repo.append_event(
                    session_id=session_id,
                    workspace_id=workspace_id,
                    org_id=org_id,
                    user_id=user_id,
                    event_type="tool_failed",
                    node=node,
                    idempotency_key=None,
                    payload={
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "failed_at": datetime.utcnow().isoformat(),
                    },
                )
            except Exception as record_error:
                logger.error(f"❌ Failed to record error event: {record_error}")

            return summary

    async def reconcile_tool_execution(
        self,
        session_id: UUID,
        tool_call_id: str,
        tool_name: str,
        verification_fn: Callable,
    ) -> bool:
        """Reconcile tool that was called but result not recorded.

        Used during resume to detect if orphaned tool_called was actually
        successful in the external system.

        Args:
            session_id: Session ID
            tool_call_id: Tool call ID from event
            tool_name: Name of tool
            verification_fn: Async function to check if tool succeeded

        Returns:
            True if tool was successfully executed, False otherwise
        """
        try:
            logger.info(f"🔍 Reconciling orphaned tool: {tool_name}")

            # Ask external system if this tool succeeded
            was_executed = await verification_fn()

            if was_executed:
                logger.info(f"✅ Confirmed tool was executed: {tool_name}")
            else:
                logger.warning(f"❌ Tool was NOT executed: {tool_name}")

            return was_executed

        except Exception as e:
            logger.error(f"❌ Reconciliation failed: {e}")
            return False
