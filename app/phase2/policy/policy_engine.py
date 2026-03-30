"""
PHASE 2: POLICY LAYER - PRODUCTION IMPLEMENTATION

Per policymain.txt: Steps 1-10
Implements policy retrieval + decision logic (traffic lights: GREEN/YELLOW/RED)
Integrated with Supabase vector DB for semantic search.
"""

import logging
import asyncio
from typing import Dict, List, Any, Optional
from enum import Enum

from app.phase1.atoms import ExecutionAtom

logger = logging.getLogger(__name__)


class PolicyDecision(str, Enum):
    """Traffic light decisions per policymain.txt Step 6"""

    GREEN = "GREEN"  # Proceed
    YELLOW = "YELLOW"  # Pause for approval
    RED = "RED"  # Block


class PolicyEngine:
    """
    Policy Layer: Retrieve + Judge workflow

    Step 5: Semantic Retrieval via Supabase
    Step 6: Deterministic Policy Judge (no LLM vibes)
    """

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client

    async def check_atom_against_policies(
        self, atom: ExecutionAtom, workspace_id: str
    ) -> Dict[str, Any]:
        """
        Main policy check: retrieve relevant rules + make decision

        Args:
            atom: ExecutionAtom from Phase 1
            workspace_id: Tenant context

        Returns:
            {
              "decision": "GREEN" | "YELLOW" | "RED",
              "reason": str,
              "matched_policies": [...],
              "matched_count": int,
              "highest_severity": "block" | "approval" | "guideline"
            }
        """
        logger.info(f"[Policy] Checking atom {atom.id}: {atom.description}")

        try:
            # ─────────────────── STEP 5: SEMANTIC RETRIEVAL ──────────────────
            # Query policies from Supabase vector DB
            matched_policies = await self._retrieve_relevant_policies(
                atom, workspace_id
            )
            logger.info(f"[Policy] Retrieved {len(matched_policies)} policies")

            if not matched_policies:
                # No relevant policies found = GREEN (proceed with warning)
                logger.info(f"[Policy] No matching policies found for atom {atom.id}")
                return {
                    "decision": PolicyDecision.GREEN.value,
                    "reason": "No relevant policies found",
                    "matched_policies": [],
                    "matched_count": 0,
                    "highest_severity": None,
                }

            # ─────────────────── STEP 6: POLICY JUDGE ──────────────────
            # Deterministic decision logic (no LLM)
            decision = self._judge_policies(atom, matched_policies)

            logger.info(
                f"[Policy] Decision: {decision['decision']} - {decision['reason']}"
            )

            # Log decision to audit trail
            try:
                from app.core.supabase_client import log_policy_decision

                await log_policy_decision(
                    workspace_id=workspace_id,
                    atom_id=atom.id,
                    policy_id=(
                        matched_policies[0].get("id", "unknown")
                        if matched_policies
                        else "none"
                    ),
                    decision=decision.get("decision", "UNKNOWN"),
                    confidence=decision.get("confidence", 0.0),
                    details=decision.get("reason", ""),
                )
            except Exception as log_err:
                logger.warning(f"[Policy] Audit log error: {log_err}")

            return decision

        except Exception as e:
            logger.error(f"[Policy] Check failed: {str(e)}", exc_info=True)
            # Fail open on infrastructure errors
            return {
                "decision": PolicyDecision.GREEN.value,
                "reason": f"Policy check error (proceeding): {str(e)}",
                "matched_policies": [],
                "matched_count": 0,
                "error": str(e),
            }

    async def _retrieve_relevant_policies(
        self, atom: ExecutionAtom, workspace_id: str
    ) -> List[Dict[str, Any]]:
        """
        Step 5: Query Supabase for relevant policies via vector similarity

        Filters:
          1. workspace_id (tenant isolation)
          2. is_active = true
          3. Cosine similarity > threshold (0.7)

        Returns top-k policies with score
        """
        try:
            from app.core.supabase_client import query_policies_vector
            from app.core.gemini_client import call_gemini_embedding

            # Generate embedding for atom description
            embedding = await call_gemini_embedding(atom.description)

            if not embedding:
                logger.warning("[Policy] Failed to generate embedding for atom")
                return []

            # Query Supabase RPC: match_policies
            # This function does cosine similarity search
            policies = await query_policies_vector(
                workspace_id=workspace_id,
                embedding=embedding,
                threshold=0.7,
                limit=5,
            )

            logger.info(f"[Policy] Vector search returned {len(policies)} policies")
            return policies

        except Exception as e:
            logger.error(f"[Policy] Retrieval failed: {str(e)}", exc_info=True)
            return []

    @staticmethod
    def _judge_policies(
        atom: ExecutionAtom, policies: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Step 6: Deterministic Policy Judge

        Converts retrieved policies into GREEN/YELLOW/RED decision.

        Logic:
          1. Filter policies by risk_scope matching atom risk_level
          2. Check enforcement type
          3. Build decision matrix

        Returns:
            {
              "decision": "GREEN|YELLOW|RED",
              "reason": str,
              "matched_policies": [{id, title, decision_reason}, ...],
              "matched_count": int,
              "highest_severity": "block|approval|guideline"
            }
        """
        if not policies:
            return {
                "decision": PolicyDecision.GREEN.value,
                "reason": "No policies matched",
                "matched_policies": [],
                "matched_count": 0,
                "highest_severity": None,
            }

        matched = []
        highest_severity = None

        for policy in policies:
            policy_type = policy.get(
                "policy_type", "guideline"
            )  # blocking, approval_required, guideline
            enforcement = policy.get("enforcement", "soft")  # hard, soft
            risk_scope = policy.get("risk_scope", "any")  # low, medium, high, any

            # Check if policy applies to this atom's risk level
            if risk_scope != "any" and atom.risk_level != risk_scope:
                continue  # Skip policies that don't apply

            # Determine severity
            if policy_type == "blocking" and enforcement == "hard":
                severity = "block"
            elif policy_type == "approval_required":
                severity = "approval"
            else:
                severity = "guideline"

            # Track highest severity
            if highest_severity is None:
                highest_severity = severity
            elif severity == "block":
                highest_severity = "block"
            elif severity == "approval" and highest_severity != "block":
                highest_severity = "approval"

            matched.append(
                {
                    "id": str(policy.get("id")),
                    "title": policy.get("title", "Unnamed Policy"),
                    "policy_type": policy_type,
                    "enforcement": enforcement,
                    "severity": severity,
                    "similarity_score": policy.get("similarity", 0.0),
                    "reason": f"Policy '{policy.get('title')}' ({policy_type})",
                }
            )

        # ─────────────────── DECISION MATRIX ──────────────────
        # Per policymain.txt Step 6

        if highest_severity == "block":
            return {
                "decision": PolicyDecision.RED.value,
                "reason": "Hard blocking policy matched",
                "matched_policies": matched,
                "matched_count": len(matched),
                "highest_severity": "block",
            }

        elif highest_severity == "approval":
            return {
                "decision": PolicyDecision.YELLOW.value,
                "reason": "Approval-required policy matched",
                "matched_policies": matched,
                "matched_count": len(matched),
                "highest_severity": "approval",
            }

        else:
            return {
                "decision": PolicyDecision.GREEN.value,
                "reason": "Guideline policy matched (proceeding with flag)",
                "matched_policies": matched,
                "matched_count": len(matched),
                "highest_severity": "guideline",
            }


# Synchronous wrapper for legacy code
def run_policy_check(atom: ExecutionAtom) -> Dict[str, Any]:
    """
    Synchronous legacy wrapper.
    Call via: asyncio.run(PolicyEngine().check_atom_against_policies(atom, workspace_id))
    """
    engine = PolicyEngine()
    loop = asyncio.get_event_loop()
    try:
        return loop.run_until_complete(
            engine.check_atom_against_policies(atom, workspace_id="default")
        )
    except RuntimeError:
        # No event loop, create one
        return asyncio.run(
            engine.check_atom_against_policies(atom, workspace_id="default")
        )

    try:
        data = eval(response)
    except Exception:
        return CheckResult(
            check_type="policy",
            status="warn",
            message="Policy unclear; human review required",
        )

    decision = data.get("decision", "soft_violation")
    reason = data.get("reason", "Policy review required")

    if decision == "pass":
        status = "pass"
    elif decision == "hard_violation":
        status = "fail"
    else:
        status = "warn"

    return CheckResult(
        check_type="policy",
        status=status,
        message=reason,
        metadata={"decision": decision},
    )
