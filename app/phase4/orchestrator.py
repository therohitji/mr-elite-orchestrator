"""
Orchestrator for Phase 4 execution.
Manages execution flow, atom sequencing, parallel execution, and feedback loops.
"""

import logging
import asyncio
from typing import List, Any, Dict, Optional, Set
from datetime import datetime
from uuid import UUID

from app.phase4.dispatcher import CentralDispatcher
from app.core.execution.context import global_context
from app.core.execution.dependencies import dependency_manager
from app.phase4.workflow_manager import WorkflowManager
from app.phase4.result_store import ResultStore
from app.phase4.dispatcher import ExecutionStatus
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# Define ExecutionResult here to avoid circular import
@dataclass
class ExecutionResult:
    """Result of atom execution."""

    atom_id: str
    status: ExecutionStatus
    output: Optional[Any] = None
    error: Optional[str] = None
    runway: Optional["ExecutionRunway"] = None
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "atom_id": self.atom_id,
            "status": (
                self.status.value if hasattr(self.status, "value") else str(self.status)
            ),
            "output": self.output,
            "error": self.error,
            "runway": self.runway.value if self.runway else None,
            "duration_ms": self.duration_ms,
        }


class ExecutionRunway(str, Enum):
    """Execution runway types per Phase 4 spec."""

    CODE_TOOL = "CODE_TOOL"  # Fast, deterministic
    AI_AGENT = "AI_AGENT"  # Slow, creative
    WORKFLOW = "WORKFLOW"  # Structured process


class ExecutionOrchestrator:
    """
    Orchestrates execution of a sequence of atoms.
    Manages the complete execution pipeline per Phase 4 spec.
    Supports parallel execution of independent atoms.
    """

    def __init__(self, org_id: str, workspace_id: str, request_id: str):
        """
        Initialize orchestrator.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            request_id: Request identifier
        """
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.request_id = request_id

        self.dispatcher = CentralDispatcher(org_id, workspace_id, request_id)
        self.workflow_manager = WorkflowManager(org_id, workspace_id)
        self.result_store = ResultStore(org_id, workspace_id)

        self._results: List[ExecutionResult] = []
        self._execution_start: Optional[datetime] = None

    async def execute_plan(
        self,
        atoms: List[Any],
        clear_context: bool = True,
        enable_checkpoints: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute a complete plan (list of atoms) with parallel execution support.

        Args:
            atoms: List of atoms to execute
            clear_context: Whether to clear global context before execution
            enable_checkpoints: Whether to save checkpoints for resumption

        Returns:
            Execution summary with results and logs
        """
        if clear_context:
            global_context.clear()
            dependency_manager.reset()

        self._results.clear()
        self._execution_start = datetime.now()

        logger.info(f"[ExecutionOrchestrator] Starting execution of {len(atoms)} atoms")

        # Register all atoms with their dependencies
        for atom in atoms:
            deps = getattr(atom, "dependencies", None) or []
            dependency_manager.register_atom(str(atom.id), deps)

        # Build dependency graph
        dependency_graph = self._build_dependency_graph(atoms)

        # Execute atoms in parallel where possible
        completed_atoms = []
        pending_atoms = [str(atom.id) for atom in atoms]
        failed_atoms = []

        while pending_atoms:
            # Find atoms ready for execution (dependencies satisfied)
            ready_atoms = self._get_ready_atoms(atoms, completed_atoms, failed_atoms)

            if not ready_atoms:
                logger.warning(
                    "[ExecutionOrchestrator] No atoms ready for execution. Possible circular dependency."
                )
                break

            logger.info(
                f"[ExecutionOrchestrator] Executing {len(ready_atoms)} atoms in parallel"
            )

            # Execute ready atoms in parallel
            results = await asyncio.gather(
                *[self.dispatcher.dispatch(atom) for atom in ready_atoms],
                return_exceptions=True,
            )

            # Process results
            for atom, result in zip(ready_atoms, results):
                atom_id_str = str(atom.id)

                if isinstance(result, Exception):
                    logger.error(
                        f"[ExecutionOrchestrator] Atom {atom.id} raised exception: {result}"
                    )
                    dependency_manager.mark_error(atom_id_str)
                    failed_atoms.append(atom_id_str)

                    exec_result = ExecutionResult(
                        atom_id=atom_id_str,
                        status=ExecutionStatus.ERROR,
                        error=str(result),
                    )
                elif result.status == ExecutionStatus.SUCCESS:
                    dependency_manager.mark_success(atom_id_str)
                    completed_atoms.append(atom_id_str)

                    exec_result = ExecutionResult(
                        atom_id=atom_id_str,
                        status=ExecutionStatus.SUCCESS,
                        output=result.output,
                        duration_ms=result.duration_ms,
                    )
                else:
                    dependency_manager.mark_error(atom_id_str)
                    failed_atoms.append(atom_id_str)

                    exec_result = ExecutionResult(
                        atom_id=atom_id_str,
                        status=ExecutionStatus.ERROR,
                        error=result.error,
                        duration_ms=result.duration_ms,
                    )

                self._results.append(exec_result)
                pending_atoms.remove(atom_id_str)

                logger.info(
                    f"[ExecutionOrchestrator] Atom {atom.id} -> {exec_result.status.value}"
                )

            # Save checkpoint if enabled
            if enable_checkpoints:
                await self.workflow_manager.save_checkpoint(
                    request_id=self.request_id,
                    completed_atoms=completed_atoms,
                    pending_atoms=pending_atoms,
                    failed_atoms=failed_atoms,
                )

        # Mark workflow complete
        if enable_checkpoints:
            if failed_atoms and not pending_atoms:
                await self.workflow_manager.fail_workflow(
                    self.request_id, f"{len(failed_atoms)} atoms failed"
                )
            elif not pending_atoms:
                await self.workflow_manager.complete_workflow(self.request_id)

        return self.get_summary()

    def get_summary(self) -> Dict[str, Any]:
        """Get execution summary."""
        total_atoms = len(self._results)
        successful = sum(
            1 for r in self._results if r.status == ExecutionStatus.SUCCESS
        )
        failed = sum(1 for r in self._results if r.status == ExecutionStatus.ERROR)
        skipped = sum(1 for r in self._results if r.status == ExecutionStatus.SKIPPED)

        duration = None
        if self._execution_start:
            duration = (datetime.now() - self._execution_start).total_seconds()

        return {
            "request_id": self.request_id,
            "total_atoms": total_atoms,
            "successful": successful,
            "failed": failed,
            "skipped": skipped,
            "duration_seconds": duration,
            "results": [r.to_dict() for r in self._results],
            "global_context": global_context.snapshot(),
            "execution_logs": global_context.get_all_logs(),
        }

    def get_result(self, atom_id: str) -> Optional[ExecutionResult]:
        """Get result for a specific atom."""
        for result in self._results:
            if result.atom_id == atom_id:
                return result
        return None

    def _build_dependency_graph(self, atoms: List[Any]) -> Dict[str, List[str]]:
        """
        Build a dependency graph from atoms.

        Args:
            atoms: List of atoms

        Returns:
            Dictionary mapping atom_id -> list of dependency atom_ids
        """
        graph = {}
        for atom in atoms:
            deps = getattr(atom, "dependencies", None) or []
            graph[str(atom.id)] = deps
        return graph

    def _get_ready_atoms(
        self, atoms: List[Any], completed: List[str], failed: List[str]
    ) -> List[Any]:
        """
        Get atoms that are ready for execution (dependencies satisfied).

        Args:
            atoms: All atoms
            completed: List of completed atom IDs
            failed: List of failed atom IDs

        Returns:
            List of atoms ready for execution
        """
        ready = []
        completed_set = set(completed)
        failed_set = set(failed)

        for atom in atoms:
            atom_id_str = str(atom.id)

            # Skip if already processed
            if atom_id_str in completed_set or atom_id_str in failed_set:
                continue

            # Check if dependencies are satisfied
            deps = getattr(atom, "dependencies", None) or []

            # Check if any dependency failed
            if any(dep in failed_set for dep in deps):
                # Mark as skipped due to failed dependency
                dependency_manager.mark_skipped(atom_id_str, "Dependency failed")
                failed.append(atom_id_str)
                self._results.append(
                    ExecutionResult(
                        atom_id=atom_id_str,
                        status=ExecutionStatus.SKIPPED,
                        error="Skipped due to failed dependency",
                    )
                )
                continue

            # Check if all dependencies are completed
            if all(dep in completed_set for dep in deps):
                ready.append(atom)

        return ready

    async def resume_from_checkpoint(
        self, request_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Resume execution from a saved checkpoint.

        Args:
            request_id: Request ID to resume (defaults to current)

        Returns:
            Execution summary
        """
        resume_id = request_id or self.request_id

        checkpoint = await self.workflow_manager.load_checkpoint(resume_id)
        if not checkpoint:
            logger.error(f"[ExecutionOrchestrator] No checkpoint found for {resume_id}")
            return {"error": "No checkpoint found"}

        # Restore context
        await self.workflow_manager.restore_context(resume_id)

        logger.info(
            f"[ExecutionOrchestrator] Resuming execution for {resume_id}: "
            f"completed={len(checkpoint['completed_atoms'])}, "
            f"pending={len(checkpoint['pending_atoms'])}"
        )

        # TODO: Resume execution logic
        # This would require reconstructing the atom list and continuing execution

        return {"message": "Resume not fully implemented", "checkpoint": checkpoint}


# Global orchestrator factory
def create_orchestrator(
    org_id: str, workspace_id: str, request_id: str
) -> ExecutionOrchestrator:
    """
    Factory function to create an orchestrator instance.

    Args:
        org_id: Organization ID
        workspace_id: Workspace ID
        request_id: Request identifier

    Returns:
        ExecutionOrchestrator instance
    """
    return ExecutionOrchestrator(org_id, workspace_id, request_id)
