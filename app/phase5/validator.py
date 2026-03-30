"""
Phase 5: Session Validator

This module implements the Post-Mortem validation logic.
It compares the Intended Plan (atoms) vs. Actual Execution Logs.

Per spec:
  - Status Check: Did every critical atom succeed?
  - Output Validation: Are the expected artifacts present?
  - Error Classification: Network/Logic/Permission/Data errors

Outputs:
  - SUCCESS: All atoms succeeded
  - PARTIAL_SUCCESS: Non-critical failures only
  - CRITICAL_FAILURE: Critical atom failed
  - BLOCKED: Security/Permission blocked
"""

import logging
from typing import Dict, List, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    """Final session status after validation"""

    SUCCESS = "COMPLETED"
    PARTIAL_SUCCESS = "PARTIAL"
    CRITICAL_FAILURE = "FAILED"
    BLOCKED = "BLOCKED"


class FailureType(str, Enum):
    """Classification of failure reasons"""

    SYSTEM_ERROR = "SYSTEM_ERROR"  # Tool crashed/timed out
    LOGIC_ERROR = "LOGIC_ERROR"  # Bad arguments from LLM
    PERMISSION_ERROR = "PERMISSION_ERROR"  # Security layer blocked
    DATA_MISSING = "DATA_MISSING"  # Search returned 0 results


class ValidationResult:
    """Result of session validation"""

    def __init__(
        self,
        status: SessionStatus,
        details: Optional[List[Dict[str, Any]]] = None,
        success_count: int = 0,
        failure_count: int = 0,
        error_summary: Optional[str] = None,
    ):
        self.status = status
        self.details = details or []
        self.success_count = success_count
        self.failure_count = failure_count
        self.error_summary = error_summary

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "status": self.status.value,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "error_summary": self.error_summary,
            "details": self.details,
        }


class SessionValidator:
    """
    The "Judge" of execution quality.

    Validates execution results against the original plan,
    classifying failures and determining overall session status.
    """

    def __init__(self):
        """Initialize validator"""
        logger.info("SessionValidator initialized")

    async def validate(
        self, session_id: str, atoms: List[Any], execution_results: List[Dict[str, Any]]
    ) -> ValidationResult:
        """
        Validate execution session.

        Args:
            session_id: Unique session identifier
            atoms: List of planned atoms (from Phase 2)
            execution_results: List of execution results (from Phase 4)

        Returns:
            ValidationResult with status and details
        """
        logger.info(f"Validating session {session_id} with {len(atoms)} atoms")

        # Build result lookup map
        result_map = {r.get("atom_id"): r for r in execution_results}

        failures = []
        success_count = 0
        failure_count = 0

        # Validate each atom
        for atom in atoms:
            atom_id = getattr(atom, "id", None)

            # Find corresponding execution result
            result = result_map.get(atom_id)

            # Check structural status
            if not result:
                failures.append(
                    {
                        "atom_id": atom_id,
                        "atom_name": getattr(atom, "tool_name", "unknown"),
                        "type": FailureType.SYSTEM_ERROR.value,
                        "reason": "No execution trace found",
                        "is_critical": getattr(atom, "is_critical", False),
                    }
                )
                failure_count += 1
                continue

            status = result.get("status", "").upper()

            if status == "ERROR" or status == "FAILED":
                # System error
                failures.append(
                    {
                        "atom_id": atom_id,
                        "atom_name": getattr(atom, "tool_name", "unknown"),
                        "type": self._classify_error(result.get("error", "")),
                        "reason": result.get("error", "Unknown error"),
                        "is_critical": getattr(atom, "is_critical", False),
                    }
                )
                failure_count += 1
                continue

            if status == "TIMEOUT":
                # Timeout error
                failures.append(
                    {
                        "atom_id": atom_id,
                        "atom_name": getattr(atom, "tool_name", "unknown"),
                        "type": FailureType.SYSTEM_ERROR.value,
                        "reason": "Execution timeout (30s limit)",
                        "is_critical": getattr(atom, "is_critical", False),
                    }
                )
                failure_count += 1
                continue

            # Check artifact integrity (silent failure check)
            expected_output_key = getattr(atom, "output_variable", None)
            output_payload = (
                result["output"]
                if isinstance(result, dict)
                else getattr(result, "output", None)
            )

            if expected_output_key and not self._validate_artifacts(
                expected_output_key, output_payload
            ):
                failures.append(
                    {
                        "atom_id": atom_id,
                        "atom_name": getattr(atom, "tool_name", "unknown"),
                        "type": FailureType.DATA_MISSING.value,
                        "reason": f"Tool ran but output missing expected key: {expected_output_key}",
                        "is_critical": getattr(atom, "is_critical", False),
                    }
                )
                failure_count += 1
                continue

            # Success
            success_count += 1

        # Calculate final verdict
        return self._calculate_verdict(failures, success_count, failure_count)

    def _classify_error(self, error_message: str) -> str:
        """
        Classify error type based on message.

        Args:
            error_message: Error string from execution

        Returns:
            FailureType string
        """
        error_lower = error_message.lower()

        # Permission errors
        if any(
            x in error_lower
            for x in [
                "permission",
                "forbidden",
                "403",
                "unauthorized",
                "401",
                "blocked",
            ]
        ):
            return FailureType.PERMISSION_ERROR.value

        # Logic errors (bad input)
        if any(
            x in error_lower
            for x in ["invalid", "validation", "type error", "bad request", "400"]
        ):
            return FailureType.LOGIC_ERROR.value

        # Data missing
        if any(x in error_lower for x in ["not found", "no results", "empty", "404"]):
            return FailureType.DATA_MISSING.value

        # Default: system error
        return FailureType.SYSTEM_ERROR.value

    def _validate_artifacts(self, expected_key: str, output: Any) -> bool:
        """
        Check if output contains expected data.

        Args:
            expected_key: Variable name we expect to find
            output: Actual output from execution

        Returns:
            True if artifact is present
        """
        if not expected_key:
            return True  # No expectation

        if output is None:
            return False

        # If output is a dict, check for key
        if isinstance(output, dict):
            return expected_key in output or len(output) > 0

        # If output is a list, check non-empty
        if isinstance(output, list):
            return len(output) > 0

        # Any other non-None value is valid
        return True

    def _calculate_verdict(
        self, failures: List[Dict[str, Any]], success_count: int, failure_count: int
    ) -> ValidationResult:
        """
        Calculate final session status.

        Args:
            failures: List of failure details
            success_count: Number of successful atoms
            failure_count: Number of failed atoms

        Returns:
            ValidationResult with final verdict
        """
        # No failures = SUCCESS
        if not failures:
            logger.info(f"Session validation: SUCCESS ({success_count} atoms)")
            return ValidationResult(
                status=SessionStatus.SUCCESS,
                success_count=success_count,
                failure_count=0,
            )

        # Check for blocked status
        blocked_failures = [
            f for f in failures if f["type"] == FailureType.PERMISSION_ERROR.value
        ]
        if blocked_failures:
            logger.warning(
                f"Session validation: BLOCKED ({len(blocked_failures)} permission errors)"
            )
            return ValidationResult(
                status=SessionStatus.BLOCKED,
                details=failures,
                success_count=success_count,
                failure_count=failure_count,
                error_summary=f"Blocked by security: {blocked_failures[0]['reason']}",
            )

        # Check if any critical atom failed
        critical_failures = [f for f in failures if f.get("is_critical", False)]
        if critical_failures:
            logger.error(
                f"Session validation: CRITICAL_FAILURE ({len(critical_failures)} critical errors)"
            )
            return ValidationResult(
                status=SessionStatus.CRITICAL_FAILURE,
                details=failures,
                success_count=success_count,
                failure_count=failure_count,
                error_summary=f"Critical atom failed: {critical_failures[0]['reason']}",
            )

        # Non-critical failures only
        logger.warning(
            f"Session validation: PARTIAL_SUCCESS ({failure_count} non-critical errors)"
        )
        return ValidationResult(
            status=SessionStatus.PARTIAL_SUCCESS,
            details=failures,
            success_count=success_count,
            failure_count=failure_count,
            error_summary=f"{failure_count} non-critical steps failed",
        )
