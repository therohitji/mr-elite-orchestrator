"""
Phase 4 Output Schemas

Standardized response structures for Phase 4 execution results.
These schemas define the format for execution_plan, formatted_response,
and other Phase 4 outputs.
"""

from typing import Any, Dict, List, Optional, Union, Literal
from pydantic import BaseModel, Field
from enum import Enum

# Import skill-specific output schemas
from app.agents.base import SkillCategory, get_skill_category


# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════

class ResponseStatus(str, Enum):
    """Standard status values for all responses."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    TIMEOUT = "TIMEOUT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NO_OPERATIONS = "NO_OPERATIONS"


class UIState(str, Enum):
    """UI state hints for frontend rendering."""
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    INFO = "info"
    APPROVAL_NEEDED = "approval_needed"
    CLARIFICATION_NEEDED = "clarification_needed"


# ═══════════════════════════════════════════════════════════════
# ERROR SCHEMA
# ═══════════════════════════════════════════════════════════════

class ErrorOutput(BaseModel):
    """Standardized error output for any operation."""
    type: str = "UnknownError"
    message: str
    operation: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    retryable: bool = False


# ═══════════════════════════════════════════════════════════════
# EXECUTION RESULT SCHEMAS
# ═══════════════════════════════════════════════════════════════

class ExecutionItemResult(BaseModel):
    """
    Result for a single executed atom.
    
    Used in the `execution_plan` list of the API response.
    """
    atom_id: str
    skill: str
    skill_category: SkillCategory = SkillCategory.GENERAL
    description: str
    status: ResponseStatus
    
    # Output data - skill-specific structured output
    output: Optional[Dict[str, Any]] = None
    
    # Display hints for frontend rendering
    display: Optional[Dict[str, Any]] = None
    
    # Error info (when status is FAILED or TIMEOUT)
    error: Optional[ErrorOutput] = None
    
    # Execution timing
    duration_ms: Optional[float] = None
    
    # Action links (e.g., {"view": "https://...", "edit": "https://..."})
    action_link: Optional[Dict[str, str]] = None
    
    # Human-readable message
    message: Optional[str] = None


class ExecutionSummary(BaseModel):
    """Summary of execution results."""
    total_executed: int
    successful: int
    failed: int
    successful_items: List[Dict[str, Any]] = Field(default_factory=list)
    failed_items: List[Dict[str, Any]] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# APPROVAL SCHEMAS
# ═══════════════════════════════════════════════════════════════

class ApprovalItemDisplay(BaseModel):
    """Display data for an approval item (skill-specific preview)."""
    # Email-specific
    to: Optional[List[str]] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    
    # Calendar-specific
    title: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    attendees: Optional[List[str]] = None
    
    # Generic
    description: Optional[str] = None


class ApprovalItem(BaseModel):
    """Single item requiring approval."""
    approval_id: str
    atom_id: str
    skill: str
    skill_category: SkillCategory = SkillCategory.GENERAL
    type: str = "policy_approval"
    action_description: str
    reason: str
    risk_level: str = "MEDIUM"
    status: Literal["PENDING", "APPROVED", "REJECTED"] = "PENDING"
    
    # Preview of what will be executed
    display: Optional[ApprovalItemDisplay] = None
    
    # Action links
    action_link: Optional[Dict[str, str]] = None


class ApprovalRequestOutput(BaseModel):
    """Collection of items needing approval."""
    total_approvals: int
    all_atoms_waiting: bool = True
    items: List[ApprovalItem] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# CLARIFICATION SCHEMAS
# ═══════════════════════════════════════════════════════════════

class ClarificationData(BaseModel):
    """Data for clarification requests."""
    input_type: str = "clarification"
    prompt: str
    capability: Optional[str] = None
    missing_fields: Optional[List[str]] = None
    collected_so_far: Optional[Dict[str, Any]] = None


class ClarificationOutput(BaseModel):
    """Clarification request output."""
    clarification_id: Optional[str] = None
    message: str
    data: ClarificationData


# ═══════════════════════════════════════════════════════════════
# FORMATTED RESPONSE (Main UI response)
# ═══════════════════════════════════════════════════════════════

class FormattedResponse(BaseModel):
    """
    The formatted_response field in API output.
    
    This is the primary field the frontend uses to render the response.
    """
    message: str
    ui_state: UIState
    status: ResponseStatus
    
    # Output data - skill-specific or aggregated
    output: Optional[Dict[str, Any]] = None
    
    # For multi-result responses
    data: Optional[Dict[str, Any]] = None
    
    # Clarification-specific
    clarification_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# TOP-LEVEL API RESPONSE
# ═══════════════════════════════════════════════════════════════

class Phase4ApiResponse(BaseModel):
    """
    Top-level API response structure for Phase 4 results.
    
    This defines the standard format for all /classify endpoint responses
    after Phase 4 execution.
    """
    status: Literal["success", "error"]
    message: Optional[str] = None
    ui_state: Optional[UIState] = None
    
    # The main formatted response for frontend
    formatted_response: Optional[FormattedResponse] = None
    
    # Execution details
    execution_plan: Optional[List[ExecutionItemResult]] = None
    execution_summary: Optional[ExecutionSummary] = None
    
    # Approval flow
    approval_request: Optional[ApprovalRequestOutput] = None
    
    # Phase debug info (can be omitted in production)
    phases: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None


# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def build_execution_item(
    atom_id: str,
    skill: str,
    description: str,
    status: ResponseStatus,
    output: Optional[Dict[str, Any]] = None,
    error: Optional[ErrorOutput] = None,
    duration_ms: Optional[float] = None,
    message: Optional[str] = None,
) -> ExecutionItemResult:
    """Helper to build an ExecutionItemResult with proper skill category."""
    return ExecutionItemResult(
        atom_id=atom_id,
        skill=skill,
        skill_category=get_skill_category(skill),
        description=description,
        status=status,
        output=output,
        error=error,
        duration_ms=duration_ms,
        message=message,
    )


def build_formatted_response(
    message: str,
    ui_state: UIState,
    status: ResponseStatus,
    output: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    clarification_id: Optional[str] = None,
) -> FormattedResponse:
    """Helper to build a FormattedResponse."""
    return FormattedResponse(
        message=message,
        ui_state=ui_state,
        status=status,
        output=output,
        data=data,
        clarification_id=clarification_id,
    )


def build_execution_summary(
    results: List[ExecutionItemResult],
) -> ExecutionSummary:
    """Build an ExecutionSummary from a list of results."""
    successful = [r for r in results if r.status == ResponseStatus.SUCCESS]
    failed = [r for r in results if r.status in (ResponseStatus.FAILED, ResponseStatus.TIMEOUT)]
    
    return ExecutionSummary(
        total_executed=len(results),
        successful=len(successful),
        failed=len(failed),
        successful_items=[
            {"atom_id": r.atom_id, "skill": r.skill, "message": r.message}
            for r in successful
        ],
        failed_items=[
            {"atom_id": r.atom_id, "skill": r.skill, "message": r.message, "error": r.error.model_dump() if r.error else None}
            for r in failed
        ],
    )
