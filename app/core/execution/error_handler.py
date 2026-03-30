"""
Error handling and custom exceptions for the Execution Layer.
"""

import logging

logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    """Base exception for execution errors."""

    def __init__(self, atom_id: str, message: str):
        self.atom_id = atom_id
        self.message = message
        super().__init__(f"Atom {atom_id}: {message}")


class TimeoutError(ExecutionError):
    """Raised when execution exceeds timeout threshold."""

    def __init__(self, atom_id: str, timeout_seconds: int):
        super().__init__(
            atom_id,
            f"Execution timeout after {timeout_seconds}s",
        )


class DependencyError(ExecutionError):
    """Raised when a required dependency fails."""

    def __init__(self, atom_id: str, failed_dependency: str):
        super().__init__(
            atom_id,
            f"Dependency '{failed_dependency}' failed. Skipping this atom.",
        )


class InputSanitizationError(ExecutionError):
    """Raised when input sanitization fails."""

    def __init__(self, atom_id: str, field: str, expected_type: str):
        super().__init__(
            atom_id,
            f"Failed to sanitize field '{field}'. Expected type: {expected_type}",
        )


class RegistryLookupError(ExecutionError):
    """Raised when tool not found in registry."""

    def __init__(self, atom_id: str, tool_name: str):
        super().__init__(
            atom_id,
            f"Tool '{tool_name}' not found in registry",
        )
