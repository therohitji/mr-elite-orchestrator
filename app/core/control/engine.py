# app/core/control/engine.py
# Legacy import for backward compatibility
# Phase 3 control is now in app/phase3/__init__.py

from app.phase3 import Phase3ControlEngine

__all__ = ["Phase3ControlEngine"]
            approval_id = await self.approval.create_approval(atom)
            return ControlResult(
                atom_id=atom_id,
                status=ControlStatus.PAUSED,
                reason=reason,
                approval_id=approval_id,
            )

        return ControlResult(
            atom_id=atom_id,
            status=ControlStatus.READY,
            modified_atom=atom.model_dump() if hasattr(atom, "model_dump") else atom,
        )
