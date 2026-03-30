# app/core/control/segment2_policy.py
from typing import Tuple
from app.core.control.policy_retriever import PolicyRetriever
from app.core.control.policy_judge import PolicyJudge

class Segment2PolicyJudge:
    def __init__(self):
        self.retriever = PolicyRetriever()
        self.judge = PolicyJudge()

    async def evaluate(self, atom) -> Tuple[bool, str]:
        policies = self.retriever.retrieve(
            atom_description=atom.description,
            domain=atom.target_agent,
        )

        for policy in policies:
            passed, reason = await self.judge.judge(
                action=atom.description,
                policy=policy,
            )
            if not passed:
                return False, f"Policy violation: {policy.id} → {reason}"

        return True, "All policies satisfied"
