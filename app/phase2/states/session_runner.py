"""Session Runner: Main execution loop with lock heartbeat

Orchestrates session resumption and execution.
Handles lock acquisition, heartbeat renewal, event replay, and crash recovery.
"""

from typing import Dict, Any, Optional, Callable
from datetime import datetime, timedelta
import asyncio
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


class SessionRunner:
    """Main orchestrator for session execution."""

    def __init__(
        self,
        states_repo,
        events_repo,
        locks_repo,
        tool_wrapper,
        worker_id: str,
    ):
        """Initialize runner.

        Args:
            states_repo: StatesRepository
            events_repo: EventsRepository
            locks_repo: LocksRepository
            tool_wrapper: ToolWrapper
            worker_id: Unique identifier for this worker
        """
        self.states_repo = states_repo
        self.events_repo = events_repo
        self.locks_repo = locks_repo
        self.tool_wrapper = tool_wrapper
        self.worker_id = worker_id
        self.heartbeat_interval = 10  # seconds
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.lock_info = None

    async def run_session(
        self,
        session_id: UUID,
        workspace_id: UUID,
        org_id: UUID,
        user_id: Optional[UUID],
        executor_fn: Callable[[Dict[str, Any]], None],
    ) -> Dict[str, Any]:
        """Run a session (acquire lock, execute, release lock).

        Main entry point. Orchestrates the full execution flow:
        1. Acquire lock
        2. Start heartbeat
        3. Load snapshot + delta events
        4. Hydrate runtime state
        5. Execute (call executor_fn with state)
        6. Checkpoint result
        7. Release lock

        Args:
            session_id: Session to run
            workspace_id: Workspace ID
            org_id: Organization ID
            user_id: User ID
            executor_fn: Async function that executes the workflow

        Returns:
            Result dict with status, final_state, error (if any)
        """
        try:
            # Step 1: Acquire lock
            logger.info(f"🔐 Acquiring lock for {session_id}")
            self.lock_info = await self.locks_repo.acquire_lock(
                session_id=session_id,
                workspace_id=workspace_id,
                locked_by=self.worker_id,
                lease_seconds=30,
            )

            if not self.lock_info:
                logger.warning(f"⚠️ Could not acquire lock for {session_id}")
                return {
                    "status": "locked",
                    "error": "Another worker holds this session",
                    "session_id": str(session_id),
                }

            # Step 2: Start heartbeat
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop(session_id))

            # Step 3: Load and hydrate state
            logger.info(f"📂 Loading session {session_id}")
            snapshot = await self.states_repo.get_session(session_id)

            if not snapshot:
                await self._cleanup_lock(session_id)
                return {
                    "status": "not_found",
                    "error": "Session not found",
                    "session_id": str(session_id),
                }

            # Step 4: Replay events
            logger.info(f"♻️ Hydrating state from {snapshot['last_event_seq']} events")
            events = await self.events_repo.get_events_since(
                session_id=session_id,
                last_sequence=snapshot["last_event_seq"],
            )

            # Apply events to state
            runtime_state = snapshot["state_data"].copy()
            from app.phase2.states.reducer import StateReducer

            runtime_state = StateReducer.apply_events(runtime_state, events)

            # Step 5: Check for orphaned tools (crash recovery)
            orphaned = await self.events_repo.find_orphaned_tool_calls(session_id)
            if orphaned:
                logger.warning(
                    f"⚠️ Found {len(orphaned)} orphaned tools, may need reconciliation"
                )
                runtime_state["orphaned_tools"] = orphaned

            # Step 6: Execute
            logger.info(f"⚙️ Executing session {session_id}")
            try:
                await executor_fn(runtime_state)
                final_state = runtime_state
                execution_status = "completed"
            except Exception as exec_error:
                logger.error(f"❌ Execution error: {exec_error}")
                final_state = runtime_state
                execution_status = "failed"
                final_state["execution_error"] = str(exec_error)

            # Step 7: Checkpoint result
            logger.info(f"💾 Checkpointing session {session_id}")
            await self.states_repo.checkpoint_snapshot(
                session_id=session_id,
                current_node=final_state.get("current_node", "unknown"),
                state_data=final_state,
                status=execution_status,
                last_event_seq=snapshot["last_event_seq"],
            )

            # Step 8: Cleanup and release lock
            await self._cleanup_lock(session_id)

            return {
                "status": execution_status,
                "session_id": str(session_id),
                "final_state": final_state,
                "execution_completed": True,
            }

        except Exception as e:
            logger.error(f"❌ Session runner error: {e}")
            await self._cleanup_lock(session_id)
            return {
                "status": "error",
                "error": str(e),
                "session_id": str(session_id),
            }

    async def resume_after_approval(
        self,
        session_id: UUID,
        workspace_id: UUID,
        org_id: UUID,
        user_id: Optional[UUID],
        decision: str,  # 'approved' | 'denied'
        notes: Optional[str],
        executor_fn: Callable[[Dict[str, Any]], None],
    ) -> Dict[str, Any]:
        """Resume session after human approval.

        Args:
            session_id: Session to resume
            workspace_id: Workspace ID
            org_id: Organization ID
            user_id: User ID
            decision: 'approved' or 'denied'
            notes: Optional approval notes
            executor_fn: Execution function

        Returns:
            Result dict
        """
        try:
            # Record approval decision
            await self.events_repo.record_approval_decision(
                session_id=session_id,
                workspace_id=workspace_id,
                org_id=org_id,
                user_id=user_id,
                decision=decision,
                notes=notes,
            )

            # Resume execution if approved
            if decision == "denied":
                await self.states_repo.fail_session(
                    session_id=session_id,
                    error_message="Approval denied by user",
                    error_type="approval_denied",
                )
                return {"status": "denied", "session_id": str(session_id)}

            # Approved, resume normal execution
            return await self.run_session(
                session_id=session_id,
                workspace_id=workspace_id,
                org_id=org_id,
                user_id=user_id,
                executor_fn=executor_fn,
            )

        except Exception as e:
            logger.error(f"❌ Resume after approval failed: {e}")
            return {"status": "error", "error": str(e)}

    async def _heartbeat_loop(self, session_id: UUID) -> None:
        """Renew lock periodically (background task).

        Runs in background while session is executing.
        Stops if renewal fails (another worker took over).

        Args:
            session_id: Session to heartbeat
        """
        try:
            while True:
                await asyncio.sleep(self.heartbeat_interval)

                if not self.lock_info:
                    logger.warning(f"❌ No lock info for heartbeat")
                    break

                renewed = await self.locks_repo.renew_lock(
                    session_id=session_id,
                    lock_token=self.lock_info.lock_token,
                    lease_seconds=30,
                )

                if not renewed:
                    logger.error(f"❌ Lost lock for {session_id}, stopping execution")
                    break

                logger.debug(f"♻️ Renewed lock for {session_id}")

        except asyncio.CancelledError:
            logger.debug(f"♻️ Heartbeat cancelled for {session_id}")
        except Exception as e:
            logger.error(f"❌ Heartbeat error: {e}")

    async def _cleanup_lock(self, session_id: UUID) -> None:
        """Release lock and stop heartbeat.

        Args:
            session_id: Session to cleanup
        """
        try:
            # Stop heartbeat
            if self.heartbeat_task:
                self.heartbeat_task.cancel()
                try:
                    await self.heartbeat_task
                except asyncio.CancelledError:
                    pass

            # Release lock
            if self.lock_info:
                released = await self.locks_repo.release_lock(
                    session_id=session_id,
                    lock_token=self.lock_info.lock_token,
                )
                if released:
                    logger.info(f"🔓 Lock released for {session_id}")
                else:
                    logger.warning(f"⚠️ Failed to release lock for {session_id}")
                self.lock_info = None

        except Exception as e:
            logger.error(f"❌ Cleanup error: {e}")
