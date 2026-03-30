import logging
from typing import Dict, Any, Optional
from app.phase1.atoms import ExecutionAtom
from app.phase2.contracts import CheckResult

logger = logging.getLogger(__name__)


class SystemState:
    """
    Global system state for hard-block checks.
    In production: Load from database
    """

    def __init__(self):
        self.budget_open = True
        self.hiring_freeze = False
        self.maintenance_mode = False
        self.data_export_blocked = False
        self.offer_approval_required = False
        self.max_concurrent_hires = 10
        self.current_hiring_count = 5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "budget_open": self.budget_open,
            "hiring_freeze": self.hiring_freeze,
            "maintenance_mode": self.maintenance_mode,
            "data_export_blocked": self.data_export_blocked,
            "offer_approval_required": self.offer_approval_required,
            "max_concurrent_hires": self.max_concurrent_hires,
            "current_hiring_count": self.current_hiring_count,
        }


# Global state instance
SYSTEM_STATE = SystemState()


class StateChecker:
    """
    Phase 2 State Layer: System state validation

    State checks are HARD BLOCKS only - prevent actions when system can't handle them.
    Examples:
    - Hiring freeze → block offer creation
    - Maintenance mode → block data export
    - Budget exhausted → block offer creation
    - Concurrent hire limit → queue offer creation
    """

    @staticmethod
    def check_state(atom: ExecutionAtom) -> Dict[str, Any]:
        """
        Check system state for blocking conditions.

        Returns: {
            "status": "pass" | "fail",
            "checks": [
                {"check": "name", "status": "pass|fail", "message": "..."}
            ],
            "blocked": bool,
            "reason": str or None
        }
        """
        logger.info(f"[State] Checking system state for {atom.tool_name}")

        state_dict = SYSTEM_STATE.to_dict()
        checks = []
        blocked = False
        reason = None

        # CHECK 1: Maintenance Mode (blocks everything)
        if SYSTEM_STATE.maintenance_mode:
            checks.append(
                {
                    "check": "maintenance_mode",
                    "status": "fail",
                    "message": "System in maintenance mode - all actions blocked",
                }
            )
            blocked = True
            reason = "System maintenance in progress"

        # CHECK 2: Hiring-related tools
        if atom.tool_name in [
            "draft_offer",
            "confirm_offer_acceptance",
            "schedule_interview",
        ]:

            # Budget check
            if not SYSTEM_STATE.budget_open:
                checks.append(
                    {
                        "check": "budget_available",
                        "status": "fail",
                        "message": "Hiring budget exhausted",
                    }
                )
                blocked = True
                reason = "Hiring budget closed"

            # Hiring freeze check
            if SYSTEM_STATE.hiring_freeze:
                checks.append(
                    {
                        "check": "hiring_freeze",
                        "status": "fail",
                        "message": "Hiring freeze active",
                    }
                )
                blocked = True
                reason = "Hiring freeze active"

            # Concurrent hire limit check
            if SYSTEM_STATE.current_hiring_count >= SYSTEM_STATE.max_concurrent_hires:
                checks.append(
                    {
                        "check": "concurrent_hire_limit",
                        "status": "fail",
                        "message": f"Max concurrent hires ({SYSTEM_STATE.max_concurrent_hires}) reached",
                    }
                )
                reason = "Concurrent hire limit reached - queue for approval"

        # CHECK 3: Data export check
        if atom.tool_name in ["export_data", "backup_data", "generate_report"]:
            if SYSTEM_STATE.data_export_blocked:
                checks.append(
                    {
                        "check": "data_export_enabled",
                        "status": "fail",
                        "message": "Data export temporarily blocked",
                    }
                )
                blocked = True
                reason = "Data export blocked for maintenance"

        # CHECK 4: Offer approval requirement
        if atom.tool_name == "draft_offer" and SYSTEM_STATE.offer_approval_required:
            checks.append(
                {
                    "check": "offer_pre_approval",
                    "status": "fail",
                    "message": "All offers require pre-approval before draft",
                }
            )
            reason = "Offers require pre-approval"

        # Add pass checks if no blocks
        if not blocked:
            checks.append(
                {
                    "check": "maintenance_mode",
                    "status": "pass",
                    "message": "System operational",
                }
            )
            checks.append(
                {
                    "check": "budget_available",
                    "status": "pass",
                    "message": "Budget available",
                }
            )
            checks.append(
                {
                    "check": "hiring_freeze",
                    "status": "pass",
                    "message": "No hiring freeze",
                }
            )

        return {
            "status": "fail" if blocked else "pass",
            "checks": checks,
            "blocked": blocked,
            "reason": reason,
            "system_state": state_dict,
        }


def run_state_check(atom: ExecutionAtom) -> Dict[str, Any]:
    """
    Run state checks for atom.

    Returns blocking conditions only - hard blocks that prevent execution.
    """
    return StateChecker.check_state(atom)


def set_system_state(key: str, value: Any) -> None:
    """
    Update system state (for testing + runtime management).
    """
    if hasattr(SYSTEM_STATE, key):
        setattr(SYSTEM_STATE, key, value)
        logger.info(f"[State] Updated {key} = {value}")
    else:
        logger.warning(f"[State] Unknown state key: {key}")


def get_system_state() -> Dict[str, Any]:
    """
    Get current system state.
    """
    return SYSTEM_STATE.to_dict()
