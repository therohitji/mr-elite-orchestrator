# app/core/control/approval_store.py

from typing import Dict
from app.core.control.approval_models import ApprovalRequest

# TEMP: in-memory store (replace with DB)
APPROVAL_STORE: Dict[str, ApprovalRequest] = {}
