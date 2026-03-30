# app/core/control/segment4_approval.py

from app.core.control.approval_service import ApprovalService

class Segment4ApprovalGate:
    def __init__(self):
        self.service = ApprovalService()

    def assess(self, atom):
        risk = atom.risk_level.lower()
        if risk in {"high", "critical"}:
            return True, "High-risk action requires human approval"
        return False, "No approval required"

    async def create_approval(self, atom):
        import uuid
        approval_id = str(uuid.uuid4())
        self.service.create(approval_id, atom)
        return approval_id
