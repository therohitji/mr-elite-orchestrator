# app/core/control/resume_service.py

from app.core.control.approval_store import APPROVAL_STORE

class ResumeService:
    def get_approval_status_map(self):
        """
        Returns:
        { approval_id: APPROVED | REJECTED }
        """
        return {
            k: v.status
            for k, v in APPROVAL_STORE.items()
        }
