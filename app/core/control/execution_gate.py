# app/core/control/execution_gate.py

from typing import Dict, List


class ExecutionGate:
    """
    FINAL gate before execution (PDF-exact)
    """

    def can_execute(
        self,
        atom_id: str,
        phase3_results: Dict[str, dict],
        approval_status: Dict[str, str],
    ) -> bool:
        result = phase3_results.get(atom_id)
        if not result:
            return False

        # BLOCKED = never execute
        if result["status"] == "BLOCKED":
            return False

        # PAUSED = require approval
        if result["status"] == "PAUSED":
            approval_id = result.get("approval_id")
            if not approval_id:
                return False
            return approval_status.get(approval_id) == "APPROVED"

        # READY = ok
        return True
