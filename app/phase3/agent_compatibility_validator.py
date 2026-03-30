"""
Phase 3: Agent Compatibility Validator
=======================================

This is the NEW validation layer that checks:
  1. Is the task allowed by policy? (existing logic)
  2. Does the assigned agent actually support this task? (NEW)
  3. Are all required arguments present? (NEW)

This prevents atoms from being routed to agents that cannot handle them.

KEY: This is ADDITIVE to existing Phase 3 logic. No breaking changes.
"""

import logging
from typing import Dict, Any, Optional, Tuple
from app.agents.agent_registry import get_registry, RiskLevel

logger = logging.getLogger(__name__)


class AgentCompatibilityValidator:
    """
    Validates that an execution atom is compatible with its assigned agent.

    This runs AFTER policy checks in Phase 3 and BEFORE Phase 4 execution.
    """

    def __init__(self):
        self.registry = get_registry()

    async def validate_atom_compatibility(
        self, atom: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Validate that an atom can be executed by its assigned agent.

        Args:
            atom: ExecutionAtom as dict/object with keys:
                - target_agent: "calendar_agent", "email_agent", etc.
                - tool_arguments: dict with arguments
                - tool_name: "List Calendar Events" or other tool name

        Note: Atoms from Phase 1 have tool_arguments, not a capability field.
        We need to infer what capability is being used based on context.

        Returns:
            (is_compatible: bool, error_message: Optional[str], suggestion: Optional[Dict])
        """

        try:
            target_agent = atom.get("target_agent")
            tool_args = atom.get("tool_arguments", {})

            # Try to get capability from tool_arguments (where atomizer puts it)
            capability_name = tool_args.get("capability") if tool_args else None

            # Fallback to looking for it elsewhere (for flexibility)
            if not capability_name:
                capability_name = atom.get("capability") or atom.get("tool_name")

            # Validation 1: Does the capability exist?
            is_valid, msg = await self._validate_capability_exists(capability_name)
            if not is_valid:
                return False, msg, None

            # Validation 2: Does the assigned agent support this capability?
            is_valid, msg, suggestion = await self._validate_agent_supports_capability(
                target_agent, capability_name
            )
            if not is_valid:
                return False, msg, suggestion

            # Validation 3: Are all required arguments provided?
            is_valid, msg = await self._validate_arguments_complete(
                capability_name, arguments
            )
            if not is_valid:
                return False, msg, None

            # All validations passed
            logger.info(
                f"[AGENT COMPATIBILITY] ✓ VALID: {target_agent} can execute {capability_name}"
            )
            return True, None, None

        except Exception as e:
            logger.error(f"[AGENT COMPATIBILITY] Validation error: {e}")
            return False, f"Validation error: {str(e)}", None

    async def _validate_capability_exists(
        self, capability_name: str
    ) -> Tuple[bool, Optional[str]]:
        """Check if capability is registered in the system"""
        if not capability_name:
            return False, "No capability specified in atom"

        cap = self.registry.get_capability(capability_name)
        if not cap:
            supported = list(self.registry._capabilities.keys())
            return (
                False,
                f"Unknown capability '{capability_name}'. Supported: {supported}",
            )

        return True, None

    async def _validate_agent_supports_capability(
        self, agent_name: str, capability_name: str
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Check if the assigned agent actually implements this capability.

        If not, suggest which agent could handle it.
        """

        cap = self.registry.get_capability(capability_name)
        if not cap:
            return False, f"Unknown capability: {capability_name}", None

        expected_agent = cap.agent_name

        # Check if assigned agent matches
        if agent_name != expected_agent:
            logger.warning(
                f"[AGENT COMPATIBILITY] ✗ MISMATCH: "
                f"Atom assigned to '{agent_name}' but capability '{capability_name}' "
                f"is implemented by '{expected_agent}'"
            )

            suggestion = {
                "issue": "agent_mismatch",
                "assigned_agent": agent_name,
                "required_agent": expected_agent,
                "capability": capability_name,
                "fix": f"Change target_agent from '{agent_name}' to '{expected_agent}'",
            }

            return (
                False,
                f"Agent '{agent_name}' does not support '{capability_name}'. "
                f"This capability requires '{expected_agent}'.",
                suggestion,
            )

        return True, None, None

    async def _validate_arguments_complete(
        self, capability_name: str, provided_args: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Check that all required arguments for this capability are provided.

        Does NOT do type checking or validation of argument values (that's Phase 4).
        Just checks presence of required keys.
        """

        is_valid, error_msg = self.registry.validate_arguments(
            capability_name, provided_args
        )

        if not is_valid:
            logger.warning(
                f"[AGENT COMPATIBILITY] ✗ INCOMPLETE ARGS: {capability_name}"
            )
            return False, error_msg

        return True, None

    async def check_risk_and_approvals(self, capability_name: str) -> Dict[str, Any]:
        """
        Check if this capability requires approvals based on risk level.

        Used by Phase 3 to determine if the atom should be blocked pending approval.

        Returns:
            {
                "requires_user_confirmation": bool,
                "requires_policy_approval": bool,
                "risk_level": "low" | "medium" | "high" | "critical",
                "reason": str
            }
        """

        cap = self.registry.get_capability(capability_name)
        if not cap:
            return {
                "requires_user_confirmation": False,
                "requires_policy_approval": False,
                "risk_level": "unknown",
                "reason": f"Unknown capability: {capability_name}",
            }

        return {
            "requires_user_confirmation": cap.requires_user_confirmation,
            "requires_policy_approval": cap.requires_policy_approval,
            "risk_level": cap.risk_level.value,
            "reason": cap.description,
        }


class PhaseThreeValidator:
    """
    Complete Phase 3 Pre-Flight Check including the NEW compatibility layer.

    This is the entry point that:
    1. Runs existing policy checks
    2. Runs NEW agent compatibility checks
    3. Determines if atom should be blocked/approved/executed
    """

    def __init__(self, org_id: str, workspace_id: Optional[str] = None):
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.compatibility_validator = AgentCompatibilityValidator()

        # Import existing validators (non-breaking)
        from app.phase3.preflight import PreFlightCheck

        self.preflight_check = PreFlightCheck(org_id=org_id, workspace_id=workspace_id)

    async def validate_execution_atom(self, atom: Dict[str, Any]) -> Dict[str, Any]:
        """
        Complete validation pipeline for an execution atom.

        Returns:
            {
                "allowed": bool,
                "status": "approved" | "blocked" | "needs_approval",
                "checks": {
                    "policy": {"passed": bool, "reason": str},
                    "compatibility": {"passed": bool, "reason": str},
                    "arguments": {"passed": bool, "reason": str},
                    "risk": {"passed": bool, "reason": str}
                },
                "error": Optional[str],
                "suggestions": Optional[Dict]
            }
        """

        logger.info(
            f"[PHASE 3 VALIDATOR] Starting validation of atom: {atom.get('id')}"
        )

        result = {
            "allowed": False,
            "status": "blocked",
            "checks": {},
            "error": None,
            "suggestions": None,
        }

        try:
            # CHECK 1: Existing Policy Validation (NON-BREAKING)
            atom_description = atom.get("description", "")
            policy_check = await self.preflight_check.check(atom_description)

            result["checks"]["policy"] = {
                "passed": policy_check.get("allowed", False),
                "reason": policy_check.get("reason", ""),
            }

            if not policy_check.get("allowed"):
                result["error"] = f"Policy violation: {policy_check.get('reason')}"
                logger.warning(
                    f"[PHASE 3 VALIDATOR] Blocked by policy: {result['error']}"
                )
                return result

            # CHECK 2: NEW Agent Compatibility Validation
            compat_passed, compat_error, compat_suggestion = (
                await self.compatibility_validator.validate_atom_compatibility(atom)
            )

            result["checks"]["compatibility"] = {
                "passed": compat_passed,
                "reason": compat_error or "Agent supports this capability",
            }

            if not compat_passed:
                result["error"] = compat_error
                result["suggestions"] = compat_suggestion
                logger.warning(
                    f"[PHASE 3 VALIDATOR] Blocked by compatibility: {compat_error}"
                )
                return result

            # CHECK 3: Risk Assessment
            capability_name = atom.get("tool_name") or atom.get("capability")
            risk_check = await self.compatibility_validator.check_risk_and_approvals(
                capability_name
            )

            result["checks"]["risk"] = {
                "passed": True,
                "risk_level": risk_check["risk_level"],
                "reason": risk_check["reason"],
            }

            # Determine final status
            if (
                risk_check["requires_policy_approval"]
                or risk_check["requires_user_confirmation"]
            ):
                result["status"] = "needs_approval"
                result["allowed"] = True  # Allowed pending approval
                logger.info(
                    f"[PHASE 3 VALIDATOR] Requires approval: {capability_name} "
                    f"(risk: {risk_check['risk_level']})"
                )
            else:
                result["status"] = "approved"
                result["allowed"] = True
                logger.info(f"[PHASE 3 VALIDATOR] ✓ APPROVED: {atom.get('id')}")

            return result

        except Exception as e:
            logger.error(f"[PHASE 3 VALIDATOR] Unexpected error: {e}")
            result["error"] = f"Validation error: {str(e)}"
            result["allowed"] = False
            return result
