"""
Phase 4: Execution Layer
Central execution engine with context management, and error handling.
"""

from .context import GlobalContext, global_context
from .error_handler import ExecutionError, TimeoutError, DependencyError

__all__ = [
    "GlobalContext",
    "global_context",
    "ExecutionError",
    "TimeoutError",
    "DependencyError",
]
