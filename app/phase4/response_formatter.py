"""
Phase 4 Response Formatter

Formats raw execution outputs into user-friendly messages for frontend display.
Uses standardized output schemas for consistent response structure.

Handles:
- Task execution results (success/error)
- General queries (chat/query intents)
- Approval requests
- Error messages
"""

import logging
from typing import Dict, Any, Optional, List

from app.phase4.dispatcher import DispatcherResult, ExecutionStatus
from app.phase4.output_schemas import (
    ResponseStatus,
    UIState,
    ErrorOutput,
    ExecutionItemResult,
    ExecutionSummary,
    FormattedResponse,
    build_execution_item,
    build_formatted_response,
    build_execution_summary,
)
from app.agents.base import get_skill_category, SkillCategory

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# STATUS MAPPING
# ═══════════════════════════════════════════════════════════════

def _map_execution_status_to_response_status(status: ExecutionStatus) -> ResponseStatus:
    """Map ExecutionStatus enum to ResponseStatus enum."""
    mapping = {
        ExecutionStatus.SUCCESS: ResponseStatus.SUCCESS,
        ExecutionStatus.FAILED: ResponseStatus.FAILED,
        ExecutionStatus.TIMEOUT: ResponseStatus.TIMEOUT,
        ExecutionStatus.PENDING: ResponseStatus.PARTIAL,
        ExecutionStatus.RUNNING: ResponseStatus.PARTIAL,
    }
    return mapping.get(status, ResponseStatus.FAILED)


def _map_status_to_ui_state(status: ResponseStatus) -> UIState:
    """Map ResponseStatus to UIState."""
    mapping = {
        ResponseStatus.SUCCESS: UIState.SUCCESS,
        ResponseStatus.FAILED: UIState.ERROR,
        ResponseStatus.TIMEOUT: UIState.ERROR,
        ResponseStatus.PARTIAL: UIState.PARTIAL,
        ResponseStatus.APPROVAL_REQUIRED: UIState.APPROVAL_NEEDED,
        ResponseStatus.CLARIFICATION_REQUIRED: UIState.CLARIFICATION_NEEDED,
        ResponseStatus.NO_OPERATIONS: UIState.INFO,
    }
    return mapping.get(status, UIState.ERROR)


class ResponseFormatter:
    """
    Formats Phase 4 execution results into user-friendly responses.
    
    Uses standardized schemas from output_schemas.py for consistent structure.
    """

    @staticmethod
    def format_dispatcher_result(
        result: DispatcherResult, 
        atom_description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Format a single DispatcherResult into user-friendly message.

        Args:
            result: DispatcherResult from execution
            atom_description: Optional description of what the atom was doing

        Returns:
            Formatted response with message and UI state (dict for backward compatibility)
        """
        if result.status == ExecutionStatus.SUCCESS:
            return ResponseFormatter._format_success(result, atom_description)
        elif result.status == ExecutionStatus.FAILED:
            return ResponseFormatter._format_error(result, atom_description)
        elif result.status == ExecutionStatus.TIMEOUT:
            return ResponseFormatter._format_timeout(result, atom_description)
        else:
            return ResponseFormatter._format_generic(result, atom_description)

    @staticmethod
    def _format_success(result: DispatcherResult, atom_description: Optional[str]) -> Dict[str, Any]:
        """Format successful execution result."""
        output = result.output
        message = ResponseFormatter._extract_message_from_output(output, atom_description)
        
        # Extract skill-specific output
        skill_output = ResponseFormatter._extract_skill_output(output)

        formatted = build_formatted_response(
            message=message,
            ui_state=UIState.SUCCESS,
            status=ResponseStatus.SUCCESS,
            output=skill_output,
            data={
                "duration_ms": result.duration_ms,
            }
        )
        return formatted.model_dump()

    @staticmethod
    def _format_error(result: DispatcherResult, atom_description: Optional[str]) -> Dict[str, Any]:
        """Format error execution result."""
        error_msg = result.error or "An error occurred during execution"
        
        # Try to make error message user-friendly
        message = ResponseFormatter._build_user_friendly_error_message(error_msg)
        
        # Build structured error output
        error_output = ErrorOutput(
            type="ExecutionError",
            message=error_msg,
            operation=atom_description,
            retryable=ResponseFormatter._is_retryable_error(error_msg),
        )

        formatted = build_formatted_response(
            message=message,
            ui_state=UIState.ERROR,
            status=ResponseStatus.FAILED,
            data={
                "error": error_output.model_dump(),
                "duration_ms": result.duration_ms,
            }
        )
        return formatted.model_dump()

    @staticmethod
    def _format_timeout(result: DispatcherResult, atom_description: Optional[str]) -> Dict[str, Any]:
        """Format timeout result."""
        message = "⏱️ The operation timed out. Please try again with a simpler request."
        
        error_output = ErrorOutput(
            type="TimeoutError",
            message=result.error or "Operation timed out",
            operation=atom_description,
            retryable=True,
        )
        
        formatted = build_formatted_response(
            message=message,
            ui_state=UIState.ERROR,
            status=ResponseStatus.TIMEOUT,
            data={
                "error": error_output.model_dump(),
                "duration_ms": result.duration_ms,
            }
        )
        return formatted.model_dump()

    @staticmethod
    def _format_generic(result: DispatcherResult, atom_description: Optional[str]) -> Dict[str, Any]:
        """Format generic/unknown status."""
        message = f"Status: {result.status.value}"
        if atom_description:
            message = f"{atom_description} - {message}"

        formatted = build_formatted_response(
            message=message,
            ui_state=UIState.INFO,
            status=_map_execution_status_to_response_status(result.status),
            data={
                "output": result.output,
                "error": result.error,
            }
        )
        return formatted.model_dump()

    @staticmethod
    def _extract_skill_output(output: Any) -> Optional[Dict[str, Any]]:
        """
        Extract structured skill-specific output from raw execution output.
        
        This normalizes the output to match our standardized schemas.
        """
        if output is None:
            return None
        
        if isinstance(output, str):
            return {"raw_message": output}
        
        if isinstance(output, dict):
            # If output contains 'output' field, extract it
            if "output" in output and isinstance(output["output"], dict):
                return output["output"]
            # If output has standard agent structure, extract the data
            if "status" in output and "output" in output:
                inner_output = output.get("output")
                if isinstance(inner_output, dict):
                    return inner_output
                elif isinstance(inner_output, str):
                    return {"raw_message": inner_output}
            # Return as-is if it's already structured
            return output
        
        return None

    @staticmethod
    def _build_user_friendly_error_message(error_msg: str) -> str:
        """Convert technical error message to user-friendly message."""
        error_lower = error_msg.lower()
        
        if "unable to access" in error_lower or "connect your" in error_lower:
            return error_msg  # Already user-friendly
        elif "missing required parameter" in error_lower:
            return f"❌ Unable to complete: {error_msg}. Please provide the missing information."
        elif "timeout" in error_lower:
            return "⏱️ The operation took too long and timed out. Please try again."
        elif "token" in error_lower or "auth" in error_lower:
            return "🔗 I'm unable to access the required service. Please ensure your account is connected in settings."
        elif "not found" in error_lower:
            return f"❌ {error_msg}. Please check the details and try again."
        else:
            return f"❌ {error_msg}"

    @staticmethod
    def _is_retryable_error(error_msg: str) -> bool:
        """Determine if an error is retryable."""
        error_lower = error_msg.lower()
        retryable_keywords = ["timeout", "rate limit", "temporary", "try again"]
        return any(kw in error_lower for kw in retryable_keywords)

    @staticmethod
    def _extract_message_from_output(output: Any, atom_description: Optional[str]) -> str:
        """
        Extract user-friendly message from execution output.

        Handles different output formats:
        - String messages (e.g., "✅ Event Updated!")
        - Dict with "output" field (e.g., {"status": "success", "output": "..."})
        - Dict with "message" field
        - Agent responses (e.g., {"status": "success", "skill": "update_event", "output": "..."})
        """
        if output is None:
            if atom_description:
                return f"✅ {atom_description} completed successfully."
            return "✅ Operation completed successfully."

        # If output is a string, use it directly
        if isinstance(output, str):
            return output

        # If output is a dict, extract message
        if isinstance(output, dict):
            # Check for common message fields (priority order)
            if "message" in output and isinstance(output["message"], str):
                return output["message"]
            if "output" in output:
                output_value = output["output"]
                if isinstance(output_value, str) and output_value.strip():
                    return output_value
                # If output is a dict with message field
                if isinstance(output_value, dict) and "message" in output_value:
                    return output_value["message"]
            if "result" in output and isinstance(output["result"], str):
                return output["result"]
            if "agent_output" in output and isinstance(output["agent_output"], str):
                return output["agent_output"]
            if "status" in output and output.get("status") == "success":
                # Try to extract from agent response
                skill = output.get("skill", "")
                if skill:
                    return f"✅ {skill.replace('_', ' ').title()} completed successfully."
                return "✅ Operation completed successfully."

        # Fallback
        if atom_description:
            return f"✅ {atom_description} completed successfully."
        return "✅ Operation completed successfully."

    @staticmethod
    def format_multiple_results(
        results: List[DispatcherResult],
        atom_descriptions: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Format multiple execution results into a combined response.

        Args:
            results: List of DispatcherResult objects
            atom_descriptions: Optional dict mapping atom_id -> description

        Returns:
            Combined formatted response (dict for backward compatibility)
        """
        if not results:
            formatted = build_formatted_response(
                message="No operations were executed.",
                ui_state=UIState.INFO,
                status=ResponseStatus.NO_OPERATIONS,
            )
            return formatted.model_dump()

        # Count successes and failures
        successes = [r for r in results if r.status == ExecutionStatus.SUCCESS]
        failures = [r for r in results if r.status != ExecutionStatus.SUCCESS]

        # Format individual results
        formatted_results = []
        messages = []

        for result in results:
            atom_id = str(result.atom_id)
            description = atom_descriptions.get(atom_id) if atom_descriptions else None
            formatted = ResponseFormatter.format_dispatcher_result(result, description)
            formatted_results.append(formatted)
            if formatted.get("message"):
                messages.append(formatted["message"])

        # Create combined message
        if len(successes) == len(results):
            # All succeeded
            if len(results) == 1:
                message = formatted_results[0]["message"]
            else:
                message = f"✅ Successfully completed {len(results)} operations."
            status = ResponseStatus.SUCCESS
            ui_state = UIState.SUCCESS
        elif len(failures) == len(results):
            # All failed
            message = f"❌ All {len(results)} operations failed."
            status = ResponseStatus.FAILED
            ui_state = UIState.ERROR
        else:
            # Mixed
            message = f"⚠️ Completed {len(successes)} of {len(results)} operations. {len(failures)} failed."
            status = ResponseStatus.PARTIAL
            ui_state = UIState.PARTIAL

        formatted = build_formatted_response(
            message=message,
            ui_state=ui_state,
            status=status,
            data={
                "results": formatted_results,
                "total": len(results),
                "succeeded": len(successes),
                "failed": len(failures),
            }
        )
        return formatted.model_dump()

    @staticmethod
    def build_execution_plan_item(
        result: DispatcherResult,
        atom: Any,  # ExecutionAtom
    ) -> ExecutionItemResult:
        """
        Build an ExecutionItemResult from a DispatcherResult and atom.
        
        This is used to populate the execution_plan array in the API response.
        """
        atom_id = str(result.atom_id)
        skill = getattr(atom, 'tool_name', None) or result.skill or "unknown"
        description = getattr(atom, 'description', None) or f"Execute {skill}"
        
        # Determine status
        status = _map_execution_status_to_response_status(result.status)
        
        # Build error output if failed
        error_output = None
        if result.status != ExecutionStatus.SUCCESS and result.error:
            error_output = ErrorOutput(
                type=result.status.value,
                message=result.error,
                operation=skill,
            )
        
        # Extract skill output
        skill_output = ResponseFormatter._extract_skill_output(result.output)
        
        # Build message
        message = ResponseFormatter._extract_message_from_output(result.output, description)
        
        return ExecutionItemResult(
            atom_id=atom_id,
            skill=skill,
            skill_category=get_skill_category(skill),
            description=description,
            status=status,
            output=skill_output,
            error=error_output,
            duration_ms=result.duration_ms,
            message=message,
        )
