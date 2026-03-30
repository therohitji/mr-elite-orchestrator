"""
Dependency management and cascade error handling.
"""

import logging
from typing import List, Set, Dict, Any, Optional

logger = logging.getLogger(__name__)


class DependencyManager:
    """
    Manages atom dependencies and cascading failures.
    Prevents execution of atoms when their dependencies fail.
    """

    def __init__(self):
        self._execution_status: Dict[str, str] = (
            {}
        )  # atom_id -> "SUCCESS"|"ERROR"|"SKIPPED"
        self._atom_dependencies: Dict[str, List[str]] = (
            {}
        )  # atom_id -> [dependency_ids]

    def register_atom(
        self, atom_id: str, dependencies: Optional[List[str]] = None
    ) -> None:
        """Register an atom with its dependencies."""
        self._atom_dependencies[atom_id] = dependencies or []

    def mark_success(self, atom_id: str) -> None:
        """Mark an atom as successfully executed."""
        self._execution_status[atom_id] = "SUCCESS"
        logger.info(f"DependencyManager: Marked {atom_id} as SUCCESS")

    def mark_error(self, atom_id: str) -> None:
        """Mark an atom as failed and cascade to dependents."""
        self._execution_status[atom_id] = "ERROR"
        logger.error(f"DependencyManager: Marked {atom_id} as ERROR")
        self._cascade_failure(atom_id)

    def mark_skipped(self, atom_id: str, reason: str) -> None:
        """Mark an atom as skipped."""
        self._execution_status[atom_id] = "SKIPPED"
        logger.warning(f"DependencyManager: Marked {atom_id} as SKIPPED ({reason})")

    def can_execute(self, atom_id: str) -> tuple[bool, Optional[str]]:
        """
        Check if an atom can be executed.
        Returns: (can_execute, reason_if_cannot)
        """
        dependencies = self._atom_dependencies.get(atom_id, [])

        for dep_id in dependencies:
            status = self._execution_status.get(dep_id)
            if status == "ERROR":
                return False, f"Dependency '{dep_id}' failed"
            if status == "SKIPPED":
                return False, f"Dependency '{dep_id}' was skipped"

        return True, None

    def _cascade_failure(self, failed_atom_id: str) -> List[str]:
        """
        Find all atoms dependent on the failed atom and cascade the failure.
        Returns list of atoms marked as skipped.
        """
        skipped = []

        for atom_id, deps in self._atom_dependencies.items():
            if failed_atom_id in deps:
                self.mark_skipped(atom_id, f"Dependency '{failed_atom_id}' failed")
                skipped.append(atom_id)
                # Recursively cascade
                skipped.extend(self._cascade_failure(atom_id))

        return skipped

    def get_status(self, atom_id: str) -> str:
        """Get execution status of an atom."""
        return self._execution_status.get(atom_id, "PENDING")

    def get_all_statuses(self) -> Dict[str, str]:
        """Get all atom statuses."""
        return self._execution_status.copy()

    def reset(self) -> None:
        """Reset all statuses (for new execution cycle)."""
        self._execution_status.clear()
        logger.info("DependencyManager: Reset all statuses")


# Global instance
dependency_manager = DependencyManager()
