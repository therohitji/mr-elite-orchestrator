# app/core/control/policy_models.py
from pydantic import BaseModel
from typing import Optional

class PolicyRule(BaseModel):
    id: str
    policy_text: str
    severity: str  # BLOCKING | WARNING
    scope: Optional[str] = "GLOBAL"
