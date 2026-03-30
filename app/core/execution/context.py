"""
Global context manager for storing and sharing execution state across atoms.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class GlobalContext:
    """
    Manages global state and variable storage across atom executions.
    Ensures atoms can access outputs from previous atoms.
    """

    def __init__(self):
        self._context: Dict[str, Any] = {}
        self._execution_log: Dict[str, Any] = {}

    def set(self, key: str, value: Any) -> None:
        """Store a value in global context."""
        self._context[key] = value
        logger.info(f"GlobalContext: Set '{key}' = {type(value).__name__}")

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from global context."""
        return self._context.get(key, default)

    def update(self, updates: Dict[str, Any]) -> None:
        """Update multiple values at once."""
        self._context.update(updates)
        logger.info(f"GlobalContext: Updated {len(updates)} keys")

    def exists(self, key: str) -> bool:
        """Check if a key exists in context."""
        return key in self._context

    def log_execution(
        self,
        atom_id: str,
        status: str,
        output: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> None:
        """Log execution result for an atom."""
        self._execution_log[atom_id] = {
            "status": status,
            "output": output,
            "error": error,
        }
        logger.info(f"Execution Log: Atom {atom_id} -> {status}")

    def get_execution_log(self, atom_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve execution log for an atom."""
        return self._execution_log.get(atom_id)

    def get_all_logs(self) -> Dict[str, Any]:
        """Get all execution logs."""
        return self._execution_log.copy()

    def clear(self) -> None:
        """Clear all context and logs."""
        self._context.clear()
        self._execution_log.clear()
        logger.info("GlobalContext: Cleared")

    def snapshot(self) -> Dict[str, Any]:
        """Return a snapshot of current context."""
        return self._context.copy()


# Global instance
global_context = GlobalContext()
