"""
Phase 4: Workflow Manager - Checkpoint management for pause/resume
Handles long-running workflows with state persistence and recovery
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID
from datetime import datetime

from app.core.supabase_client import supabase_client
from app.core.execution.context import global_context

logger = logging.getLogger(__name__)


class WorkflowStatus:
    """Workflow execution status constants"""

    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WorkflowManager:
    """
    Manages workflow execution state for pause/resume functionality.
    Provides checkpoint management and recovery after failures.
    """

    def __init__(self, org_id: str, workspace_id: str):
        """
        Initialize workflow manager.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
        """
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.supabase = supabase_client

    async def save_checkpoint(
        self,
        request_id: str,
        completed_atoms: List[str],
        pending_atoms: List[str],
        failed_atoms: List[str],
        context_snapshot: Optional[Dict[str, Any]] = None,
        status: str = WorkflowStatus.IN_PROGRESS,
    ) -> Optional[str]:
        """
        Save a workflow checkpoint for resumption.

        Args:
            request_id: Request identifier
            completed_atoms: List of completed atom IDs
            pending_atoms: List of pending atom IDs
            failed_atoms: List of failed atom IDs
            context_snapshot: Global context snapshot
            status: Workflow status (IN_PROGRESS|PAUSED|COMPLETED|FAILED)

        Returns:
            Checkpoint ID or None
        """
        try:
            # Use current global context if not provided
            if context_snapshot is None:
                context_snapshot = global_context.snapshot()

            response = self.supabase.rpc(
                "save_checkpoint",
                {
                    "p_request_id": request_id,
                    "p_org_id": self.org_id,
                    "p_workspace_id": self.workspace_id,
                    "p_completed_atoms": completed_atoms,
                    "p_pending_atoms": pending_atoms,
                    "p_failed_atoms": failed_atoms,
                    "p_global_context": context_snapshot,
                    "p_status": status,
                },
            ).execute()

            checkpoint_id = response.data
            logger.info(
                f"[WorkflowManager] Saved checkpoint for request {request_id}: "
                f"completed={len(completed_atoms)}, pending={len(pending_atoms)}, "
                f"failed={len(failed_atoms)}, status={status}"
            )
            return checkpoint_id

        except Exception as e:
            logger.error(
                f"[WorkflowManager] Failed to save checkpoint for {request_id}: {e}",
                exc_info=True,
            )
            return None

    async def load_checkpoint(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Load a workflow checkpoint for resumption.

        Args:
            request_id: Request identifier

        Returns:
            Checkpoint dictionary or None
        """
        try:
            response = (
                self.supabase.table("workflow_checkpoints")
                .select("*")
                .eq("request_id", request_id)
                .eq("org_id", self.org_id)
                .eq("workspace_id", self.workspace_id)
                .single()
                .execute()
            )

            if response.data:
                logger.info(
                    f"[WorkflowManager] Loaded checkpoint for request {request_id}: "
                    f"status={response.data.get('status')}"
                )
                return response.data

            logger.warning(
                f"[WorkflowManager] No checkpoint found for request {request_id}"
            )
            return None

        except Exception as e:
            logger.error(
                f"[WorkflowManager] Failed to load checkpoint for {request_id}: {e}",
                exc_info=True,
            )
            return None

    async def pause_workflow(self, request_id: str) -> bool:
        """
        Pause a workflow execution.

        Args:
            request_id: Request identifier

        Returns:
            True if paused successfully
        """
        try:
            self.supabase.table("workflow_checkpoints").update(
                {
                    "status": WorkflowStatus.PAUSED,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            ).eq("request_id", request_id).eq("org_id", self.org_id).eq(
                "workspace_id", self.workspace_id
            ).execute()

            logger.info(f"[WorkflowManager] Paused workflow for request {request_id}")
            return True

        except Exception as e:
            logger.error(
                f"[WorkflowManager] Failed to pause workflow {request_id}: {e}",
                exc_info=True,
            )
            return False

    async def resume_workflow(self, request_id: str) -> bool:
        """
        Resume a paused workflow.

        Args:
            request_id: Request identifier

        Returns:
            True if resumed successfully
        """
        try:
            self.supabase.table("workflow_checkpoints").update(
                {
                    "status": WorkflowStatus.IN_PROGRESS,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            ).eq("request_id", request_id).eq("org_id", self.org_id).eq(
                "workspace_id", self.workspace_id
            ).eq(
                "status", WorkflowStatus.PAUSED
            ).execute()

            logger.info(f"[WorkflowManager] Resumed workflow for request {request_id}")
            return True

        except Exception as e:
            logger.error(
                f"[WorkflowManager] Failed to resume workflow {request_id}: {e}",
                exc_info=True,
            )
            return False

    async def complete_workflow(self, request_id: str) -> bool:
        """
        Mark a workflow as completed.

        Args:
            request_id: Request identifier

        Returns:
            True if marked successfully
        """
        try:
            self.supabase.table("workflow_checkpoints").update(
                {
                    "status": WorkflowStatus.COMPLETED,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            ).eq("request_id", request_id).eq("org_id", self.org_id).eq(
                "workspace_id", self.workspace_id
            ).execute()

            logger.info(f"[WorkflowManager] Marked workflow {request_id} as COMPLETED")
            return True

        except Exception as e:
            logger.error(
                f"[WorkflowManager] Failed to complete workflow {request_id}: {e}",
                exc_info=True,
            )
            return False

    async def fail_workflow(self, request_id: str, reason: str) -> bool:
        """
        Mark a workflow as failed.

        Args:
            request_id: Request identifier
            reason: Failure reason

        Returns:
            True if marked successfully
        """
        try:
            checkpoint = await self.load_checkpoint(request_id)
            if checkpoint:
                failed_atoms = checkpoint.get("failed_atoms", [])

                self.supabase.table("workflow_checkpoints").update(
                    {
                        "status": WorkflowStatus.FAILED,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                ).eq("request_id", request_id).eq("org_id", self.org_id).eq(
                    "workspace_id", self.workspace_id
                ).execute()

            logger.error(
                f"[WorkflowManager] Marked workflow {request_id} as FAILED: {reason}"
            )
            return True

        except Exception as e:
            logger.error(
                f"[WorkflowManager] Failed to mark workflow {request_id} as failed: {e}",
                exc_info=True,
            )
            return False

    async def restore_context(self, request_id: str) -> bool:
        """
        Restore global context from a checkpoint.

        Args:
            request_id: Request identifier

        Returns:
            True if context restored successfully
        """
        checkpoint = await self.load_checkpoint(request_id)
        if not checkpoint:
            return False

        try:
            context_data = checkpoint.get("global_context", {})
            global_context.update(context_data)

            logger.info(
                f"[WorkflowManager] Restored {len(context_data)} context variables "
                f"for request {request_id}"
            )
            return True

        except Exception as e:
            logger.error(
                f"[WorkflowManager] Failed to restore context for {request_id}: {e}",
                exc_info=True,
            )
            return False

    async def get_workflow_status(self, request_id: str) -> Optional[str]:
        """
        Get the current status of a workflow.

        Args:
            request_id: Request identifier

        Returns:
            Status string or None
        """
        checkpoint = await self.load_checkpoint(request_id)
        return checkpoint.get("status") if checkpoint else None

    async def list_paused_workflows(self) -> List[Dict[str, Any]]:
        """
        List all paused workflows for this org/workspace.

        Returns:
            List of paused workflow checkpoints
        """
        try:
            response = (
                self.supabase.table("workflow_checkpoints")
                .select("*")
                .eq("org_id", self.org_id)
                .eq("workspace_id", self.workspace_id)
                .eq("status", WorkflowStatus.PAUSED)
                .order("updated_at", desc=True)
                .execute()
            )

            return response.data or []

        except Exception as e:
            logger.error(
                f"[WorkflowManager] Failed to list paused workflows: {e}",
                exc_info=True,
            )
            return []
