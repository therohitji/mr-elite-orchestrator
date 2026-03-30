"""
Phase 4: Execution Layer
Main execution orchestrator, dispatcher, result models, and utilities.
"""

from typing import Any, List, Optional, Dict
from dataclasses import dataclass, field
from enum import Enum

# Import key components
from .dispatcher import CentralDispatcher, ExecutionStatus, DispatcherResult
from .orchestrator import ExecutionOrchestrator, create_orchestrator
from .result_store import ResultStore
from .retry_engine import RetryEngine, RetryConfig
from .workflow_manager import WorkflowManager, WorkflowStatus


class ExecutionRunway(str, Enum):
    """Execution runway types per Phase 4 spec."""

    CODE_TOOL = "CODE_TOOL"  # Fast, deterministic
    AI_AGENT = "AI_AGENT"  # Slow, creative
    WORKFLOW = "WORKFLOW"  # Structured process


@dataclass
class ExecutionResult:
    """Result of atom execution."""

    atom_id: str
    status: ExecutionStatus
    output: Optional[Any] = None
    error: Optional[str] = None
    runway: Optional[ExecutionRunway] = None
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


@dataclass
class AtomExecutionConfig:
    """Configuration for executing an atom."""

    timeout_seconds: int = 30
    input_schema: Optional[Dict[str, str]] = None
    output_variable: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)


@dataclass
class ToolConfig:
    """Configuration for a registered tool/agent/workflow."""

    name: str
    runway_type: ExecutionRunway
    handler: Any  # Callable function or graph
    system_prompt: Optional[str] = None
    input_schema: Optional[Dict[str, str]] = None
    description: str = ""

    def to_registry_format(self) -> Dict[str, Any]:
        """Convert to format expected by dispatcher registry."""
        return {
            "type": self.runway_type.value,
            "function": self.handler,
            "system_prompt": self.system_prompt,
            "input_schema": self.input_schema,
            "description": self.description,
        }


# Import output schemas
from .output_schemas import (
    ResponseStatus,
    UIState,
    ErrorOutput,
    ExecutionItemResult,
    ExecutionSummary,
    ApprovalItem,
    ApprovalRequestOutput,
    ClarificationData,
    ClarificationOutput,
    FormattedResponse,
    Phase4ApiResponse,
    build_execution_item,
    build_formatted_response,
    build_execution_summary,
)


__all__ = [
    # Core Components
    "CentralDispatcher",
    "ExecutionOrchestrator",
    "create_orchestrator",
    # Storage & Retry
    "ResultStore",
    "RetryEngine",
    "RetryConfig",
    # Workflow Management
    "WorkflowManager",
    "WorkflowStatus",
    # Enums & Status
    "ExecutionStatus",
    "ExecutionRunway",
    "ResponseStatus",
    "UIState",
    # Data Models
    "ExecutionResult",
    "DispatcherResult",
    "AtomExecutionConfig",
    "ToolConfig",
    # Output Schemas
    "ErrorOutput",
    "ExecutionItemResult",
    "ExecutionSummary",
    "ApprovalItem",
    "ApprovalRequestOutput",
    "ClarificationData",
    "ClarificationOutput",
    "FormattedResponse",
    "Phase4ApiResponse",
    # Helper Functions
    "build_execution_item",
    "build_formatted_response",
    "build_execution_summary",
]

