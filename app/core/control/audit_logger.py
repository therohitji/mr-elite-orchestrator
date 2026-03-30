# app/core/control/audit_logger.py
"""
Security Audit Logger: The Black Box for all control layer events
Production-ready: Supabase-backed, structured logging, compliance-ready
"""

from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID
import logging
from app.core.supabase_client import supabase_client

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, org_id: str, workspace_id: Optional[str] = None):
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.supabase = supabase_client

    async def log_event(
        self,
        event_type: str,  # PRIVACY_GUARD, SECURITY_GUARD, RELIABILITY_GUARD, APPROVAL_GATE, POLICY_VIOLATION
        severity: str,  # INFO, WARNING, ERROR, CRITICAL
        atom_id: str,
        atom_payload: Dict[str, Any],
        triggered_by: str,  # Pattern name, rule ID, or guard name
        decision: str,  # BLOCKED, REDACTED, APPROVED, PAUSED, FLAGGED
        reason: str,
        agent_role: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Log security event to database

        Returns:
            Log ID if successful, None otherwise
        """
        try:

            def convert_uuids_to_strings(data):
                if isinstance(data, dict):
                    return {k: convert_uuids_to_strings(v) for k, v in data.items()}
                elif isinstance(data, list):
                    return [convert_uuids_to_strings(v) for v in data]
                elif isinstance(data, UUID):
                    return str(data)
                elif isinstance(data, datetime):
                    return data.isoformat()
                return data

            atom_payload = convert_uuids_to_strings(atom_payload)
            metadata = convert_uuids_to_strings(metadata or {})

            result = self.supabase.rpc(
                "log_security_event",
                {
                    "p_org_id": self.org_id,
                    "p_workspace_id": self.workspace_id,
                    "p_event_type": event_type,
                    "p_severity": severity,
                    "p_atom_id": atom_id,
                    "p_atom_payload": atom_payload,
                    "p_triggered_by": triggered_by,
                    "p_decision": decision,
                    "p_reason": reason,
                    "p_agent_role": agent_role,
                    "p_user_id": user_id,
                    "p_metadata": metadata,
                },
            ).execute()

            if result.data:
                log_id = str(result.data)
                logger.info(
                    f"Audit log created: {log_id} | {event_type} | {decision} | {reason}"
                )
                return log_id
            else:
                logger.error("Failed to create audit log: No ID returned")
                return None

        except Exception as e:
            # Never fail the control flow due to logging errors
            logger.error(f"Audit logging failed (non-fatal): {e}")
            return None

    # Legacy method for backward compatibility
    def log(
        self,
        atom_id: str,
        segment: str,
        decision: str,
        reason: str = "",
    ):
        """
        Legacy sync logging method (prints to stdout)
        Kept for backward compatibility with existing code
        """
        record = {
            "atom_id": atom_id,
            "segment": segment,
            "decision": decision,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
        }
        print("[AUDIT]", record)

    async def log_privacy_guard(
        self,
        atom_id: str,
        atom_payload: Dict[str, Any],
        pii_detected: list,
        decision: str,  # BLOCKED or REDACTED
        agent_role: Optional[str] = None,
    ) -> Optional[str]:
        """Log Privacy Guard event"""
        return await self.log_event(
            event_type="PRIVACY_GUARD",
            severity="WARNING" if decision == "REDACTED" else "ERROR",
            atom_id=atom_id,
            atom_payload=atom_payload,
            triggered_by="PII_DETECTION",
            decision=decision,
            reason=f"PII detected: {', '.join(pii_detected)}",
            agent_role=agent_role,
            metadata={"pii_types": pii_detected},
        )

    async def log_security_guard(
        self,
        atom_id: str,
        atom_payload: Dict[str, Any],
        injection_pattern: str,
        decision: str,  # BLOCKED
        agent_role: Optional[str] = None,
    ) -> Optional[str]:
        """Log Security Guard event"""
        return await self.log_event(
            event_type="SECURITY_GUARD",
            severity="CRITICAL",
            atom_id=atom_id,
            atom_payload=atom_payload,
            triggered_by=f"INJECTION_PATTERN: {injection_pattern}",
            decision=decision,
            reason=f"Prompt injection detected: {injection_pattern}",
            agent_role=agent_role,
            metadata={"pattern": injection_pattern},
        )

    async def log_reliability_guard(
        self,
        atom_id: str,
        atom_payload: Dict[str, Any],
        limit_type: str,  # "rate_limit" or "budget_limit"
        limit_value: Any,
        decision: str,  # BLOCKED or PAUSED
        agent_role: Optional[str] = None,
    ) -> Optional[str]:
        """Log Reliability Guard event"""
        return await self.log_event(
            event_type="RELIABILITY_GUARD",
            severity="WARNING",
            atom_id=atom_id,
            atom_payload=atom_payload,
            triggered_by=f"{limit_type.upper()}_EXCEEDED",
            decision=decision,
            reason=f"{limit_type} exceeded: {limit_value}",
            agent_role=agent_role,
            metadata={"limit_type": limit_type, "limit_value": limit_value},
        )

    async def log_approval_gate(
        self,
        atom_id: str,
        atom_payload: Dict[str, Any],
        risk_level: str,
        risk_reason: str,
        decision: str,  # PAUSED, APPROVED, REJECTED
        approval_id: Optional[str] = None,
        agent_role: Optional[str] = None,
        decided_by: Optional[str] = None,
    ) -> Optional[str]:
        """Log Approval Gate event"""
        severity_map = {"HIGH": "WARNING", "CRITICAL": "ERROR", "MEDIUM": "INFO"}

        metadata = {"risk_level": risk_level}
        if approval_id:
            metadata["approval_id"] = approval_id

        return await self.log_event(
            event_type="APPROVAL_GATE",
            severity=severity_map.get(risk_level, "INFO"),
            atom_id=atom_id,
            atom_payload=atom_payload,
            triggered_by=f"RISK_LEVEL: {risk_level}",
            decision=decision,
            reason=risk_reason,
            agent_role=agent_role,
            user_id=decided_by,
            metadata=metadata,
        )

    async def log_policy_violation(
        self,
        atom_id: str,
        atom_payload: Dict[str, Any],
        policy_id: str,
        policy_title: str,
        decision: str,  # BLOCKED
        agent_role: Optional[str] = None,
    ) -> Optional[str]:
        """Log Policy Violation event"""
        return await self.log_event(
            event_type="POLICY_VIOLATION",
            severity="ERROR",
            atom_id=atom_id,
            atom_payload=atom_payload,
            triggered_by=f"POLICY: {policy_id}",
            decision=decision,
            reason=f"Policy violated: {policy_title}",
            agent_role=agent_role,
            metadata={"policy_id": policy_id, "policy_title": policy_title},
        )

    async def get_logs(
        self,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        atom_id: Optional[str] = None,
        limit: int = 100,
    ) -> list:
        """Query audit logs with filters"""
        try:
            query = (
                self.supabase.table("security_audit_logs")
                .select("*")
                .eq("org_id", self.org_id)
                .order("created_at", desc=True)
                .limit(limit)
            )

            if event_type:
                query = query.eq("event_type", event_type)
            if severity:
                query = query.eq("severity", severity)
            if atom_id:
                query = query.eq("atom_id", atom_id)

            result = query.execute()
            return result.data or []

        except Exception as e:
            logger.error(f"Failed to query audit logs: {e}")
            return []
