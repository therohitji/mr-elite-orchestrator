from pydantic import BaseModel
from typing import List, Literal, Dict
from uuid import UUID


class CheckResult(BaseModel):
    check_type: Literal["policy", "resource", "state", "memory"]
    status: Literal["pass", "warn", "fail"]
    message: str
    metadata: Dict = {}  # TODO(PHASE-2): similarity, rule_id, entity_id


class AtomEvaluationResult(BaseModel):
    atom_id: UUID

    # Phase-2 PDF FINAL statuses
    final_status: Literal[
        "READY",                # safe to execute
        "REQUIRES_APPROVAL",    # soft violation
        "BLOCKED",              # hard violation
        "FAILED",               # missing resource
    ]

    checks: List[CheckResult]
    tags: List[str] = []
    metadata: Dict = {}  # TODO(PHASE-3): approval info, judge output
