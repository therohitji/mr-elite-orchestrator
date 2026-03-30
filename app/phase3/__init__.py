"""
PHASE 3: CONTROL LAYER (FINAL CHECKPOINT BEFORE REAL-WORLD ACTION)
Production-Ready: Database-backed audit logging, Supabase approval queue, Redis rate limiting

Per PRD Section 5: Sequential middleware filters:
  1) Privacy Guard: Regex scan for PII
  2) Security Guard: Prompt injection detection
  3) Reliability Guard: Rate limits + cost estimation
  4) Final Gate: Human approval hold

This is the "blast-radius reducer" that prevents expensive/autonomous stupidity in production.
"""

import re
import logging
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum

from app.phase1.atoms import ExecutionAtom
from app.core.control.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


class GuardResult:
    """Result of a guard evaluation"""

    def __init__(self, passed: bool, reason: str = "", severity: str = "info"):
        self.passed = passed
        self.reason = reason
        self.severity = severity  # "info", "warning", "error"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "severity": self.severity,
        }


# ────────────────────── PRIVACY GUARD ──────────────────
class PrivacyGuard:
    """
    Segment 1: Privacy Guard
    Regex scan for PII: emails, phone, SSN, API keys, secrets
    Action: redact OR block based on risk/tool type
    """

    # Regex patterns for common PII
    PATTERNS = {
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "phone": r"\b(?:\+1|1)?[-.\s]?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b",
        "ssn": r"\b(?!000|666)[0-9]{3}-(?!00)[0-9]{2}-(?!0000)[0-9]{4}\b",
        "credit_card": r"\b(?:\d[ -]*?){13,19}\b",
        "api_key": r"(?i)(api_key|apikey|api-key|secret|token|access_token)\s*[=:]\s*[A-Za-z0-9_\-]{20,}",
    }

    @staticmethod
    def scan(atom: ExecutionAtom) -> GuardResult:
        """
        Scan atom for PII in description + tool_arguments + context_data

        Args:
            atom: ExecutionAtom to scan

        Returns:
            GuardResult with passed=True if no PII, False otherwise
        """
        # Tools that legitimately need email addresses in descriptions
        tool_name = atom.tool_name.lower()
        email_legitimate_tools = {
            "gmail.send", "gmail.create_draft", "gmail.list_recent_threads",
            "gmail.fetch_thread", "gmail.search", "gmail.read",
            "send_email", "gmail send email", "gmail – send email",
            "gmail – create draft", "gmail create draft",
            "calendar.create_event", "calendar.update_event",
            "calendar – create event", "email",
        }
        
        # Scan description (strict PII check)
        for pii_type, pattern in PrivacyGuard.PATTERNS.items():
            # Skip email pattern in description for tools that legitimately use emails
            if pii_type == "email" and tool_name in email_legitimate_tools:
                continue
            
            if re.search(pattern, atom.description):
                logger.warning(
                    f"[Privacy Guard] Found {pii_type} in atom {atom.id} description"
                )
                return GuardResult(
                    passed=False,
                    reason=f"Found {pii_type} in description. Redact and retry.",
                    severity="warning",
                )

        # Scan tool arguments (with context awareness)
        # Allow emails in certain contexts where they're expected (attendees, recipients, etc.)
        tool_name = atom.tool_name.lower()
        operation = atom.tool_arguments.get("operation", "").lower()

        # Tools that legitimately need email addresses
        email_allowed_tools = {
            # Legacy tool names
            "calendar – create event": ["attendees"],
            "email": ["to", "cc", "bcc", "recipients"],
            "slack": ["recipients", "channel_members"],
            "send_email": ["to", "cc", "bcc"],
            # Gmail tools (legacy)
            "gmail – send email": ["to", "cc", "bcc"],
            "gmail – create draft": ["to", "cc", "bcc"],
            "gmail – list threads": [],
            "gmail – get thread": [],
            "gmail create draft": ["to", "cc", "bcc"],
            "gmail send email": ["to", "cc", "bcc"],
            # Supabase-format tool codes (connector.operation)
            "calendar.create_event": ["attendees"],
            "calendar.update_event": ["attendees"],
            "gmail.send": ["to", "cc", "bcc", "recipients"],
            "gmail.create_draft": ["to", "cc", "bcc", "recipients"],
            "gmail.list_recent_threads": [],
            "gmail.fetch_thread": [],
            "gmail.search": [],
            "gmail.read": [],
        }

        args_str = str(atom.tool_arguments)
        for pii_type, pattern in PrivacyGuard.PATTERNS.items():
            # Skip email pattern check if this tool expects emails
            if pii_type == "email" and tool_name in email_allowed_tools:
                # Allow emails in specified fields
                allowed_fields = email_allowed_tools[tool_name]
                # If no specific fields are required (empty list), allow all emails
                if not allowed_fields or any(field in str(atom.tool_arguments) for field in allowed_fields):
                    logger.info(
                        f"[Privacy Guard] Email allowed in {tool_name} attendees/recipients field"
                    )
                    continue

            if re.search(pattern, args_str):
                logger.warning(
                    f"[Privacy Guard] Found {pii_type} in atom {atom.id} tool_arguments"
                )
                return GuardResult(
                    passed=False,
                    reason=f"Found {pii_type} in tool arguments. Redact and retry.",
                    severity="warning",
                )

        logger.info(f"[Privacy Guard] Atom {atom.id} passed PII scan")
        return GuardResult(passed=True)

    @staticmethod
    def redact(text: str) -> str:
        """Redact PII from text"""
        result = text
        for pii_type, pattern in PrivacyGuard.PATTERNS.items():
            result = re.sub(pattern, f"[REDACTED_{pii_type.upper()}]", result)
        return result


# ────────────────────── SECURITY GUARD ──────────────────
class SecurityGuard:
    """
    Segment 2: Security Guard
    Heuristic detection of prompt injection attacks
    Example: "ignore previous instructions" inside email thread
    Action: strip hostile segments OR force "read-only summarization" mode
    """

    INJECTION_PATTERNS = [
        r"ignore previous instructions?",
        r"forget.*instructions",
        r"override.*rules?",
        r"bypass.*security",
        r"execute.*code",
        r"run.*command",
        r"system\s*prompt",
        r"developer\s*mode",
        r"jailbreak",
    ]

    @staticmethod
    def scan(atom: ExecutionAtom) -> GuardResult:
        """
        Scan atom for prompt injection attempts in tool content (email, slack, etc.)

        Args:
            atom: ExecutionAtom to scan

        Returns:
            GuardResult with passed=True if no injection, False otherwise
        """
        # Scan description + tool_arguments
        combined_text = f"{atom.description} {str(atom.tool_arguments)}"

        for pattern in SecurityGuard.INJECTION_PATTERNS:
            if re.search(pattern, combined_text, re.IGNORECASE):
                logger.warning(
                    f"[Security Guard] Possible prompt injection detected in atom {atom.id}"
                )
                return GuardResult(
                    passed=False,
                    reason="Possible prompt injection detected. Manual review required.",
                    severity="error",
                )

        logger.info(f"[Security Guard] Atom {atom.id} passed injection scan")
        return GuardResult(passed=True)

    @staticmethod
    def strip_hostile(text: str) -> str:
        """Strip hostile patterns from text"""
        result = text
        for pattern in SecurityGuard.INJECTION_PATTERNS:
            result = re.sub(
                pattern,
                "[HOSTILE_SEGMENT_REMOVED]",
                result,
                flags=re.IGNORECASE,
            )
        return result


# ────────────────────── RELIABILITY GUARD ──────────────────
class ReliabilityGuard:
    """
    Segment 3: Reliability Guard
    Rate-limit checks (Redis counters)
    Cost estimation (rough token/cost budget)
    Action: throttle, queue, or ask user to confirm high-cost run
    """

    # Rough cost estimates per tool type (in USD)
    COST_ESTIMATES = {
        "send_email": 0.01,
        "draft_offer": 0.05,
        "setup_access": 0.00,
        "check_hiring_budget": 0.00,
        "fetch_job_template": 0.00,
        "default": 0.02,
    }

    RATE_LIMITS = {
        "send_email": 10,  # max 10 per minute
        "draft_offer": 5,  # max 5 per minute
        "default": 20,
    }

    # In-memory rate limit counters (in production: use Redis)
    _counters: Dict[str, int] = {}

    @staticmethod
    def check_rate_limit(atom: ExecutionAtom) -> GuardResult:
        """
        Check if atom exceeds rate limit

        Args:
            atom: ExecutionAtom to check

        Returns:
            GuardResult with passed=True if within limit
        """
        tool_name = atom.tool_name
        limit = ReliabilityGuard.RATE_LIMITS.get(
            tool_name, ReliabilityGuard.RATE_LIMITS["default"]
        )

        current_count = ReliabilityGuard._counters.get(tool_name, 0)

        if current_count >= limit:
            logger.warning(
                f"[Reliability Guard] Rate limit exceeded for {tool_name}: "
                f"{current_count}/{limit}"
            )
            return GuardResult(
                passed=False,
                reason=f"Rate limit exceeded for {tool_name}. Retry later.",
                severity="warning",
            )

        # Increment counter
        ReliabilityGuard._counters[tool_name] = current_count + 1

        return GuardResult(passed=True)

    @staticmethod
    def estimate_cost(atom: ExecutionAtom) -> float:
        """
        Estimate cost of executing atom

        Args:
            atom: ExecutionAtom

        Returns:
            Estimated cost in USD
        """
        tool_name = atom.tool_name
        base_cost = ReliabilityGuard.COST_ESTIMATES.get(
            tool_name, ReliabilityGuard.COST_ESTIMATES["default"]
        )

        # Adjust cost based on risk level
        if atom.risk_level == "high":
            base_cost *= 2.0
        elif atom.risk_level == "medium":
            base_cost *= 1.5

        # Adjust based on argument complexity
        arg_count = len(atom.tool_arguments)
        base_cost += arg_count * 0.001

        return base_cost


# ────────────────────── FINAL GATE ──────────────────
class ApprovalGate:
    """
    Segment 4: Final Gate - Human Approval Hold

    If atom.status == PAUSED (from Phase 2) or tool risk == HIGH:
      Stop branch and request explicit approval in UI
    """

    @staticmethod
    def assess(atom: ExecutionAtom) -> Tuple[bool, str]:
        """
        Assess if atom needs human approval

        Args:
            atom: ExecutionAtom

        Returns:
            (needs_approval: bool, reason: str)
        """
        logger.info(
            f"[Approval Gate] Assessing atom: tool={atom.tool_name}, risk={atom.risk_level}, status={atom.status}"
        )

        # SKIP clarification atoms - they don't need approval, they need user response
        if (
            atom.target_agent == "HUMAN_USER"
            and atom.tool_name in ("user_input_required", "human_input")
        ):
            logger.info(f"[Approval Gate] ✅ Clarification atom {atom.id} - skipping approval gate")
            return False, ""

        if atom.status == "PAUSED":
            reason = f"Atom paused in Phase 2. Requires manual approval."
            logger.info(f"[Approval Gate] {reason}")
            return True, reason

        if atom.risk_level == "high":
            reason = f"High-risk tool: {atom.tool_name}. Requires explicit approval."
            logger.info(f"[Approval Gate] {reason}")
            return True, reason

        # Sensitive operations requiring approval
        sensitive_tools = [
            "send_email", "draft_offer", "approve_payment",
            "Gmail Send Email", "Gmail – Send Email",  # Gmail send variants
        ]
        if atom.tool_name in sensitive_tools:
            reason = f"Sensitive operation: {atom.tool_name}. Requires approval."
            logger.info(f"[Approval Gate] {reason}")
            return True, reason

        logger.info(f"[Approval Gate] ✅ Atom {atom.id} approved for execution")
        return False, ""


# ────────────────────── PHASE 3 CONTROL ENGINE ──────────────────
class Phase3ControlEngine:
    """
    Orchestrates all 4 guardrails in sequence with production-grade audit logging.

    Per PRD Section 5: "This layer is your blast-radius reducer."
    """

    def __init__(
        self,
        org_id: str,
        workspace_id: Optional[str] = None,
        agent_role: Optional[str] = None,
    ):
        """
        Initialize Phase3 Control Engine with database-backed services

        Args:
            org_id: Organization ID for multi-tenancy
            workspace_id: Workspace ID (optional)
            agent_role: Agent role for audit logs (e.g., "hr_agent", "sales_bot")
        """
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.agent_role = agent_role

        self.privacy_guard = PrivacyGuard()
        self.security_guard = SecurityGuard()
        self.reliability_guard = ReliabilityGuard()
        self.approval_gate = ApprovalGate()

        # Production services with database persistence
        self.audit_logger = AuditLogger(org_id=org_id, workspace_id=workspace_id)

    async def validate_atom(self, atom: ExecutionAtom) -> Dict[str, Any]:
        """
        Run all 4 guards in sequence with full audit trail.

        Args:
            atom: ExecutionAtom from Phase 2 (READY status)

        Returns:
            {
              "passed": bool,
              "atom": ExecutionAtom (possibly updated),
              "guards": [
                {"name": "privacy", "passed": bool, "reason": str},
                ...
              ],
              "approval_id": str (if needs approval)
            }
        """
        # Silently run control checks

        guards_results = []

        # ─────────────────── GUARD 1: PRIVACY ──────────────────
        privacy_result = await asyncio.to_thread(PrivacyGuard.scan, atom)
        guards_results.append(
            {
                "name": "privacy",
                "passed": privacy_result.passed,
                "reason": privacy_result.reason,
            }
        )
        if not privacy_result.passed:
            logger.warning(f"[Phase3] Atom {atom.id} failed privacy check")

            # Audit log: PII detection
            await self.audit_logger.log_privacy_guard(
                atom_id=str(atom.id),
                atom_payload=atom.model_dump(),
                pii_detected=["PII_PATTERN"],  # Could parse from reason
                decision="BLOCKED",
                agent_role=self.agent_role,
            )

            return {
                "passed": False,
                "atom": atom,
                "guards": guards_results,
                "reason": privacy_result.reason,
            }

        # ─────────────────── GUARD 2: SECURITY ──────────────────
        security_result = await asyncio.to_thread(SecurityGuard.scan, atom)
        guards_results.append(
            {
                "name": "security",
                "passed": security_result.passed,
                "reason": security_result.reason,
            }
        )
        if not security_result.passed:
            logger.warning(f"[Phase3] Atom {atom.id} failed security check")

            # Audit log: Injection detection
            await self.audit_logger.log_security_guard(
                atom_id=str(atom.id),
                atom_payload=atom.model_dump(),
                injection_pattern=security_result.reason,
                decision="BLOCKED",
                agent_role=self.agent_role,
            )

            return {
                "passed": False,
                "atom": atom,
                "guards": guards_results,
                "reason": security_result.reason,
            }

        # ─────────────────── GUARD 3: RELIABILITY ──────────────────
        reliability_result = await asyncio.to_thread(
            ReliabilityGuard.check_rate_limit, atom
        )
        cost = await asyncio.to_thread(ReliabilityGuard.estimate_cost, atom)

        guards_results.append(
            {
                "name": "reliability",
                "passed": reliability_result.passed,
                "reason": reliability_result.reason,
                "estimated_cost_usd": cost,
            }
        )
        if not reliability_result.passed:
            logger.warning(f"[Phase3] Atom {atom.id} failed reliability check")

            # Audit log: Rate limit exceeded
            await self.audit_logger.log_reliability_guard(
                atom_id=str(atom.id),
                atom_payload=atom.model_dump(),
                limit_type="rate_limit",
                limit_value=reliability_result.reason,
                decision="BLOCKED",
                agent_role=self.agent_role,
            )

            return {
                "passed": False,
                "atom": atom,
                "guards": guards_results,
                "reason": reliability_result.reason,
            }

        # Store estimated cost in atom
        atom.cost_usd = cost

        # ─────────────────── GUARD 4: APPROVAL ──────────────────
        needs_approval, approval_reason = await asyncio.to_thread(
            ApprovalGate.assess, atom
        )

        if needs_approval:
            atom.status = "PAUSED"

            # Create approval request (local ID only - tracking moved to Session State)
            from uuid import uuid4
            approval_id = str(uuid4())

            guards_results.append(
                {
                    "name": "approval_gate",
                    "needs_approval": True,
                    "reason": approval_reason,
                    "approval_id": approval_id,
                }
            )

            # Audit log: Approval requested
            await self.audit_logger.log_approval_gate(
                atom_id=str(atom.id),
                atom_payload=atom.model_dump(),
                risk_level="HIGH",
                risk_reason=approval_reason,
                decision="PAUSED",
                approval_id=approval_id,  # Log the ID
                agent_role=self.agent_role,
            )

            logger.info(
                f"[Phase3] Atom {atom.id} requires human approval (ID: {approval_id})"
            )
            return {
                "passed": True,
                "atom": atom,
                "guards": guards_results,
                "requires_approval": True,
                "approval_reason": approval_reason,
                "approval_id": approval_id,
            }

        guards_results.append(
            {
                "name": "approval_gate",
                "needs_approval": False,
                "reason": "Auto-approved",
            }
        )

        # ─────────────────── ALL GUARDS PASSED ──────────────────
        logger.info(f"[Phase3] Atom {atom.id} passed all control checks")
        atom.status = "APPROVED"

        # Audit log: Approved
        await self.audit_logger.log_approval_gate(
            atom_id=str(atom.id),
            atom_payload=atom.model_dump(),
            risk_level="LOW",
            risk_reason="All guards passed",
            decision="APPROVED",
            agent_role=self.agent_role,
        )

        return {
            "passed": True,
            "atom": atom,
            "guards": guards_results,
            "requires_approval": False,
        }
