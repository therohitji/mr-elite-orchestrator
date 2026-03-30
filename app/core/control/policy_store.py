# app/core/control/policy_store.py
from app.core.control.policy_models import PolicyRule

# TEMP: hardcoded policies (replace with pgvector later)
POLICIES = [
    PolicyRule(
        id="HR_SALARY_LIMIT",
        policy_text="Engineering offers must not exceed ₹60L total compensation without CFO approval.",
        severity="BLOCKING",
        scope="GLOBAL"
    ),
    PolicyRule(
        id="HR_APPROVAL_REQUIRED",
        policy_text="All offer letters must be approved by HR Director before being sent.",
        severity="BLOCKING",
        scope="GLOBAL"
    ),
]
