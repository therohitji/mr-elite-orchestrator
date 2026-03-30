# app/core/control/policy_retriever.py
from typing import List
from app.core.control.policy_models import PolicyRule
from app.core.control.policy_store import POLICIES

class PolicyRetriever:
    def retrieve(self, atom_description: str, domain: str) -> List[PolicyRule]:
        """
        TEMP LOGIC:
        - Return all policies matching domain
        Later:
        - Replace with vector similarity search
        """
        return [
            p for p in POLICIES
            if p.scope == "GLOBAL" or p.scope.lower() == domain.lower()
        ]
