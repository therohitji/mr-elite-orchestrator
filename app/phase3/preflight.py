# app/phase3/preflight.py
"""
Pre-Flight Check (Layer 0): Ultra-fast blocking BEFORE Phase 1 planning
Production-ready: Vector search against high-level policies, minimal token cost

Purpose: Block obviously forbidden requests before expensive Phase 1 processing
Example: "Delete all customer data" → INSTANT BLOCK (no planning needed)
"""

from typing import Dict, Any, Optional
import logging
from app.core.supabase_client import supabase_client
from app.phase2.policy.policy_engine import PolicyEngine

logger = logging.getLogger(__name__)


class PreFlightCheck:
    """
    Layer 0: Pre-flight check before Phase 1 planning

    Performs ultra-fast vector search against high-level policies to block
    obviously forbidden requests before expensive CoT planning.
    """

    def __init__(self, org_id: str, workspace_id: Optional[str] = None):
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.supabase = supabase_client
        self.policy_engine = PolicyEngine(org_id=org_id, workspace_id=workspace_id)

    async def check(
        self, user_input: str, context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Pre-flight check: Semantic search against high-level policies

        Args:
            user_input: Raw user request (before atomization)
            context: Optional context (user_role, time, location, etc.)

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "matched_policies": List[Dict] (if blocked)
            }
        """
        try:
            logger.info(f"[PreFlight] Checking request: {user_input[:100]}...")

            # Vector search against BLOCK-type policies only
            # This is much faster than full policy evaluation
            matching_policies = await self._quick_policy_scan(user_input, context)

            if matching_policies:
                # Found BLOCK policy match
                blocked_policy = matching_policies[0]

                logger.warning(
                    f"[PreFlight] BLOCKED by policy: {blocked_policy['title']}"
                )

                return {
                    "allowed": False,
                    "reason": f"Request blocked by policy: {blocked_policy['title']}",
                    "matched_policies": matching_policies,
                    "severity": "HIGH",
                }

            # Check for hardcoded danger patterns (instant block)
            danger_check = self._check_danger_patterns(user_input)
            if not danger_check["allowed"]:
                logger.warning(
                    f"[PreFlight] BLOCKED by danger pattern: {danger_check['reason']}"
                )
                return danger_check

            # All clear
            logger.info("[PreFlight] Request passed pre-flight checks")
            return {
                "allowed": True,
                "reason": "No blocking policies found",
            }

        except Exception as e:
            # Never fail on pre-flight - let Phase 1 proceed
            logger.error(f"[PreFlight] Error during check (allowing request): {e}")
            return {
                "allowed": True,
                "reason": "Pre-flight check failed (defaulting to allow)",
                "error": str(e),
            }

    async def _quick_policy_scan(
        self, query: str, context: Optional[Dict[str, Any]] = None
    ) -> list:
        """
        Quick semantic search against BLOCK-type policies

        Uses vector similarity search in Supabase (pg_vector)
        Only checks policies with action="BLOCK" and high impact
        """
        try:
            # Use PolicyEngine's semantic search capability
            # but filter to only BLOCK-type, high-impact policies
            results = await self.policy_engine.semantic_search_policies(
                query=query,
                top_k=3,
                similarity_threshold=0.8,  # High threshold for pre-flight
                action_filter="BLOCK",
            )

            return results

        except Exception as e:
            logger.error(f"[PreFlight] Policy scan failed: {e}")
            return []

    def _check_danger_patterns(self, text: str) -> Dict[str, Any]:
        """
        Hardcoded danger pattern check (regex-based)

        These are instant-block patterns for extreme cases:
        - Data deletion commands
        - System destruction
        - Mass operations without limits
        """
        DANGER_PATTERNS = [
            # Data destruction
            (
                r"delete\s+(all|every|entire)\s+(customer|user|database|data)",
                "Attempted mass data deletion",
            ),
            (r"drop\s+(table|database|schema)", "Attempted database destruction"),
            (r"truncate\s+(table|all)", "Attempted table truncation"),
            # System destruction
            (r"(rm|remove)\s+-rf\s+/", "Attempted system file deletion"),
            (r"shutdown|reboot|restart\s+system", "Attempted system shutdown"),
            # Mass operations
            (
                r"send\s+(email|message)\s+to\s+(all|everyone|entire)",
                "Attempted mass communication without approval",
            ),
            (
                r"approve\s+all\s+(requests|payments|orders)",
                "Attempted mass approval without review",
            ),
            # Security bypass
            (
                r"bypass\s+(security|authentication|approval)",
                "Attempted security bypass",
            ),
            (
                r"disable\s+(security|logging|audit)",
                "Attempted security control disable",
            ),
        ]

        import re

        for pattern, reason in DANGER_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "allowed": False,
                    "reason": reason,
                    "severity": "CRITICAL",
                    "matched_pattern": pattern,
                }

        return {"allowed": True}

    async def check_with_context(
        self,
        user_input: str,
        user_role: str,
        user_id: str,
        time_of_day: str,
        location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Context-aware pre-flight check

        Considers:
        - User role (admin, member, guest)
        - Time of day (business hours, night, weekend)
        - Location (office, remote, international)
        """
        context = {
            "user_role": user_role,
            "user_id": user_id,
            "time_of_day": time_of_day,
            "location": location,
        }

        # Basic check first
        result = await self.check(user_input, context)

        # Additional context-based blocks
        if result["allowed"]:
            # Example: Block high-risk operations outside business hours for non-admins
            if user_role != "admin" and time_of_day in ["night", "weekend"]:
                if any(
                    keyword in user_input.lower()
                    for keyword in [
                        "approve payment",
                        "send money",
                        "delete",
                        "fire",
                        "terminate",
                    ]
                ):
                    return {
                        "allowed": False,
                        "reason": f"High-risk operation blocked outside business hours for non-admin users",
                        "severity": "MEDIUM",
                        "requires_admin": True,
                    }

        return result


# ────────────────────── HELPER FUNCTION ──────────────────
async def preflight_check(
    user_input: str,
    org_id: str,
    workspace_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Convenience function for quick pre-flight check

    Usage:
        result = await preflight_check("Delete all customers", org_id="org_123")
        if not result["allowed"]:
            return {"error": result["reason"]}
    """
    checker = PreFlightCheck(org_id=org_id, workspace_id=workspace_id)
    return await checker.check(user_input, context)
