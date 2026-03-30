# app/core/control/models.py
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel


class ControlStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"


class ControlResult(BaseModel):
    atom_id: str
    status: ControlStatus
    reason: Optional[str] = None
    modified_atom: Optional[Dict[str, Any]] = None
    approval_id: Optional[str] = None
