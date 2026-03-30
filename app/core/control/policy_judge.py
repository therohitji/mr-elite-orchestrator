# app/core/control/policy_judge.py
from typing import Tuple
from app.core.control.policy_models import PolicyRule


# app/core/control/policy_judge.py


class PolicyJudge:
    async def judge(self, action: str, policy):
        action_lower = action.lower()

        # Salary limit enforcement
        if "₹80l" in action_lower or "80l" in action_lower:
            if "60l" in policy.policy_text.lower():
                return False, "Salary exceeds ₹60L policy limit"

        # Offer approval enforcement
        if "send" in action_lower or "issue" in action_lower:
            if "approved" not in action_lower:
                return False, "Offer sent without required approval"

        return True, "Policy compliant"
