# app/routers/phase3.py
"""
Phase 3 Router: Control Layer API Endpoints

Provides:
1. Reusable `run_phase3_pipeline()` function for main.py integration
2. Isolated endpoints to test Phase 3 components

Core Function:
- run_phase3_pipeline: Executes full Phase 3 (Privacy → Security → Reliability → Approval Gate)

Test Endpoints:
- POST /phase3/test - Full Phase 3 with debug output
- POST /phase3/validate - Single atom validation
- POST /phase3/privacy - Privacy scan only
- POST /phase3/security - Security scan only
- GET /phase3/pending - List pending approvals
"""

import logging
from typing import Optional, List, Dict, Any
from uuid import UUID
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

# Import shared schemas and utilities
from app.routers.shared import AtomInput, serialize_uuid, atom_input_to_execution_atom
from app.core.session import get_session_service
from app.core.session.state_schema import SessionStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/phase3", tags=["Phase 3 - Control"])


# ─────────────────── PHASE 3 RESULT DATACLASS ──────────────────

@dataclass
class Phase3Result:
    """Result from running Phase 3 pipeline"""
    # Status
    success: bool
    
    # Approved atoms (atoms that passed all guards)
    approved_atoms: List[Any] = field(default_factory=list)
    
    # Blocked atoms (atoms that failed guards)
    blocked_atoms: List[Any] = field(default_factory=list)
    
    # Atoms requiring approval
    pending_approval_atoms: List[Any] = field(default_factory=list)
    
    # Validation results per atom
    validation_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # Summary counts
    passed_count: int = 0
    blocked_count: int = 0
    approval_required_count: int = 0
    
    # Guard statistics
    privacy_failures: int = 0
    security_failures: int = 0
    reliability_failures: int = 0
    
    # Error info
    error: Optional[str] = None
    error_type: Optional[str] = None


# ─────────────────── CORE PIPELINE FUNCTION ──────────────────

async def run_phase3_pipeline(
    atoms: List[Any],  # List[ExecutionAtom]
    org_id: str = "default_org",
    workspace_id: str = "default",
    agent_role: Optional[str] = None,
) -> Phase3Result:
    """
    Execute the complete Phase 3 pipeline.
    
    This is the core function that main.py calls. It returns a Phase3Result
    containing atoms that passed control checks and those that need approval.
    
    Per PRD Section 5 - Phase 3 runs 4 sequential guards:
      1. Privacy Guard: Regex scan for PII
      2. Security Guard: Heuristic detection of prompt injection
      3. Reliability Guard: Rate-limit checks and cost estimation
      4. Approval Gate: Human approval if needed
    
    Args:
        atoms: List of enriched ExecutionAtom from Phase 2
        org_id: Organization ID for multi-tenancy
        workspace_id: Workspace ID for Supabase lookups
        agent_role: Optional agent role for audit logs
        
    Returns:
        Phase3Result with approved atoms, blocked atoms, and pending approvals
    """
    from app.phase3 import Phase3ControlEngine
    
    try:
        # Initialize Phase 3 Control Engine
        control_engine = Phase3ControlEngine(
            org_id=org_id,
            workspace_id=workspace_id,
            agent_role=agent_role,
        )
        
        # Track results
        approved_atoms = []
        blocked_atoms = []
        pending_approval_atoms = []
        validation_results = []
        
        # Guard failure counters
        privacy_failures = 0
        security_failures = 0
        reliability_failures = 0
        
        # Process each atom through Phase 3 guards
        for atom in atoms:
            try:
                result = await control_engine.validate_atom(atom)
                validation_results.append(result)
                
                if result.get("requires_approval"):
                    pending_approval_atoms.append(result.get("atom", atom))
                elif result.get("passed"):
                    approved_atoms.append(result.get("atom", atom))
                else:
                    blocked_atoms.append(result.get("atom", atom))
                    
                    # Track which guard failed
                    guards = result.get("guards", [])
                    for guard in guards:
                        if not guard.get("passed"):
                            guard_name = guard.get("name", "")
                            if guard_name == "privacy":
                                privacy_failures += 1
                            elif guard_name == "security":
                                security_failures += 1
                            elif guard_name == "reliability":
                                reliability_failures += 1
                
                logger.debug(
                    f"[Phase3] Atom {atom.id} validated: passed={result.get('passed')}, "
                    f"requires_approval={result.get('requires_approval', False)}"
                )
                
            except Exception as atom_error:
                logger.error(f"[Phase3] Failed to validate atom {atom.id}: {atom_error}")
                validation_results.append({
                    "atom_id": str(atom.id),
                    "passed": False,
                    "error": str(atom_error),
                })
                blocked_atoms.append(atom)
        
        passed_count = len(approved_atoms)
        blocked_count = len(blocked_atoms)
        approval_required_count = len(pending_approval_atoms)
        
        logger.info(
            f"[Phase3] Pipeline complete: {len(atoms)} atoms validated "
            f"(passed={passed_count}, blocked={blocked_count}, approval_needed={approval_required_count})"
        )
        
        return Phase3Result(
            success=True,
            approved_atoms=approved_atoms,
            blocked_atoms=blocked_atoms,
            pending_approval_atoms=pending_approval_atoms,
            validation_results=validation_results,
            passed_count=passed_count,
            blocked_count=blocked_count,
            approval_required_count=approval_required_count,
            privacy_failures=privacy_failures,
            security_failures=security_failures,
            reliability_failures=reliability_failures,
        )
        
    except Exception as e:
        logger.error(f"Phase 3 pipeline error: {e}", exc_info=True)
        return Phase3Result(
            success=False,
            error=str(e),
            error_type=type(e).__name__,
        )


# ─────────────────── REQUEST SCHEMAS ──────────────────
# Note: AtomInput is imported from app.routers.shared


class Phase3Request(BaseModel):
    """Request payload for Phase 3 testing"""
    atoms: List[AtomInput]
    org_id: str = "default_org"
    workspace_id: str = "default"
    agent_role: Optional[str] = None


class ValidateSingleRequest(BaseModel):
    """Request for single atom validation"""
    atom: AtomInput
    org_id: str = "default_org"
    workspace_id: str = "default"


class PrivacyScanRequest(BaseModel):
    """Request for privacy scan only"""
    text: str
    include_tool_args: Optional[Dict[str, Any]] = None


class SecurityScanRequest(BaseModel):
    """Request for security scan only"""
    text: str
    include_tool_args: Optional[Dict[str, Any]] = None


# ─────────────────── ALIASED HELPERS ──────────────────
# These are aliased from shared module for backward compatibility
_serialize_uuid = serialize_uuid
_atom_to_execution_atom = atom_input_to_execution_atom


# ─────────────────── ENDPOINTS ──────────────────

@router.post("/test")
async def test_phase3_complete(request: Phase3Request):
    """
    🧪 TEST PHASE 3 COMPLETE
    
    Runs the full Phase 3 pipeline in isolation using run_phase3_pipeline().
    Returns all intermediate results for debugging.
    
    **Use this to verify:**
    - Privacy guard is detecting PII correctly
    - Security guard is catching injection attempts
    - Reliability guard is enforcing rate limits
    - Approval gate is triggering for high-risk actions
    """
    try:
        # Convert input atoms to ExecutionAtom objects
        atoms = [_atom_to_execution_atom(a) for a in request.atoms]
        
        # Run the pipeline
        result = await run_phase3_pipeline(
            atoms=atoms,
            org_id=request.org_id,
            workspace_id=request.workspace_id,
            agent_role=request.agent_role,
        )
        
        # Build detailed response
        response = {
            "request": {
                "atom_count": len(request.atoms),
                "org_id": request.org_id,
                "workspace_id": request.workspace_id,
            },
            "status": "success" if result.success else "error",
            "summary": {
                "total_atoms": len(atoms),
                "passed": result.passed_count,
                "blocked": result.blocked_count,
                "approval_required": result.approval_required_count,
            },
            "guard_failures": {
                "privacy": result.privacy_failures,
                "security": result.security_failures,
                "reliability": result.reliability_failures,
            },
        }
        
        # Add approved atom details
        response["approved_atoms"] = [
            {
                "atom_id": str(atom.id),
                "tool_name": atom.tool_name,
                "description": atom.description,
                "status": atom.status,
            }
            for atom in result.approved_atoms
        ]
        
        # Add blocked atom details
        response["blocked_atoms"] = [
            {
                "atom_id": str(atom.id),
                "tool_name": atom.tool_name,
                "description": atom.description,
                "status": atom.status,
            }
            for atom in result.blocked_atoms
        ]
        
        # Add pending approval details
        response["pending_approval_atoms"] = [
            {
                "atom_id": str(atom.id),
                "tool_name": atom.tool_name,
                "description": atom.description,
                "status": atom.status,
                "risk_level": atom.risk_level,
            }
            for atom in result.pending_approval_atoms
        ]
        
        # Add validation results
        response["validation_results"] = [_serialize_uuid(r) for r in result.validation_results]
        
        # Add error info if present
        if result.error:
            response["error"] = result.error
            response["error_type"] = result.error_type
        
        return JSONResponse(content=jsonable_encoder(response))
        
    except Exception as e:
        logger.error(f"Phase 3 test failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=jsonable_encoder({
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            })
        )


@router.post("/validate")
async def validate_single_atom(request: ValidateSingleRequest):
    """
    🔍 VALIDATE SINGLE ATOM
    
    Run Phase 3 validation on a single atom.
    Shows all guard results and final decision.
    
    Useful for debugging individual atom control checks.
    """
    try:
        from app.phase3 import Phase3ControlEngine
        
        # Convert to ExecutionAtom
        atom = _atom_to_execution_atom(request.atom)
        
        # Run validation
        engine = Phase3ControlEngine(
            org_id=request.org_id,
            workspace_id=request.workspace_id,
        )
        result = await engine.validate_atom(atom)
        
        return {
            "atom_id": str(atom.id),
            "tool_name": atom.tool_name,
            "description": atom.description,
            "passed": result.get("passed"),
            "requires_approval": result.get("requires_approval", False),
            "approval_id": result.get("approval_id"),
            "approval_reason": result.get("approval_reason"),
            "guards": result.get("guards", []),
            "reason": result.get("reason"),
        }
        
    except Exception as e:
        logger.error(f"Validation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/privacy")
async def check_privacy_only(request: PrivacyScanRequest):
    """
    🔒 PRIVACY SCAN ONLY
    
    Run just the privacy guard to detect PII.
    Shows what patterns were matched.
    
    Useful for testing PII detection rules.
    """
    try:
        from app.phase3 import PrivacyGuard
        from app.phase1.atoms import ExecutionAtom
        from uuid import uuid4
        
        # Create minimal atom for scanning
        tool_args = request.include_tool_args or {}
        atom = ExecutionAtom(
            id=uuid4(),
            tool_name="test_tool",
            target_agent="test_agent",
            description=request.text,
            tool_arguments=tool_args,
        )
        
        # Run privacy scan
        result = PrivacyGuard.scan(atom)
        
        return {
            "text": request.text[:200] + "..." if len(request.text) > 200 else request.text,
            "passed": result.passed,
            "reason": result.reason,
            "severity": result.severity,
            "redacted_sample": PrivacyGuard.redact(request.text[:500]),
        }
        
    except Exception as e:
        logger.error(f"Privacy check failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/security")
async def check_security_only(request: SecurityScanRequest):
    """
    🛡️ SECURITY SCAN ONLY
    
    Run just the security guard to detect prompt injection.
    Shows what hostile patterns were matched.
    
    Useful for testing injection detection rules.
    """
    try:
        from app.phase3 import SecurityGuard
        from app.phase1.atoms import ExecutionAtom
        from uuid import uuid4
        
        # Create minimal atom for scanning
        tool_args = request.include_tool_args or {"content": request.text}
        atom = ExecutionAtom(
            id=uuid4(),
            tool_name="test_tool",
            target_agent="test_agent",
            description=request.text,
            tool_arguments=tool_args,
        )
        
        # Run security scan
        result = SecurityGuard.scan(atom)
        
        return {
            "text": request.text[:200] + "..." if len(request.text) > 200 else request.text,
            "passed": result.passed,
            "reason": result.reason,
            "severity": result.severity,
            "stripped_sample": SecurityGuard.strip_hostile(request.text[:500]),
        }
        
    except Exception as e:
        logger.error(f"Security check failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending")
async def list_pending_approvals():
    """Deprecated: Approvals are now managed within session state."""
    return {"status": "deprecated", "message": "Please check your active session for pending approvals."}


@router.get("/status")
async def phase3_status():
    """
    📊 PHASE 3 STATUS
    
    Check Phase 3 engine status and configuration.
    """
    try:
        from app.phase3 import (
            Phase3ControlEngine,
            PrivacyGuard,
            SecurityGuard,
            ReliabilityGuard,
            ApprovalGate,
        )
        
        return {
            "status": "healthy",
            "guards": {
                "privacy": {
                    "enabled": True,
                    "patterns_count": len(PrivacyGuard.PATTERNS),
                },
                "security": {
                    "enabled": True,
                    "patterns_count": len(SecurityGuard.INJECTION_PATTERNS),
                },
                "reliability": {
                    "enabled": True,
                    "rate_limit_per_minute": 60,
                },
                "approval_gate": {
                    "enabled": True,
                },
            },
            "description": "Phase 3 Control Layer - Privacy + Security + Reliability + Approval Gate",
        }
        
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


# ─────────────────── APPROVAL DECISION ENDPOINT ──────────────────


class ApprovalDecisionPayload(BaseModel):
    """Payload for approving/rejecting an atom"""
    approval_id: str
    decision: str  # "approve" or "reject"
    session_id: str  # Required for finding the session
    comment: Optional[str] = None
    decided_by: Optional[str] = None
    workspace_id: Optional[str] = None


@router.post("/approve")
async def approve_atom(
    payload: ApprovalDecisionPayload
):
    """
    ✅ APPROVE OR REJECT ATOM
    
    Decision flow:
      1. Load Session from workflow_states
      2. Find approval item in session state
      3. Update status (APPROVED/REJECTED)
      4. Execute synchronously if all approvals granted
    """
    from datetime import datetime
    from app.phase1.atoms import ExecutionAtom
    from app.core.session.session_service import get_session_service
    from app.phase4.dispatcher import CentralDispatcher, DispatcherResult
    from uuid import UUID, uuid4
    import asyncio
    
    try:
        workspace_id = payload.workspace_id or "default"
        user_id = payload.decided_by or "system"
        approval_id = payload.approval_id
        session_id = payload.session_id
        decision = payload.decision.lower()

        if decision not in ["approve", "reject"]:
            return {
                "status": "error",
                "error": f"Invalid decision: {decision}. Use 'approve' or 'reject'",
            }

        # 1. Load Session
        session_service = get_session_service(org_id="default", workspace_id=workspace_id)
        session = await session_service.load_session(session_id)
        
        if not session:
             raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        # 2. Find Approval Item in Session
        approval_entry = session.approvals.entries.get(approval_id)
        if not approval_entry:
             # Try finding by atom_id if approval_id matches atom_id (fallback)
             # But let's assume strict approval_id first.
             raise HTTPException(status_code=404, detail=f"Approval ID {approval_id} not found in session")

        # 3. Update Decision
        # We need to update the entry status.
        # But SessionService doesn't have a granular "update_approval_status" method exposed?
        # We can modify session object and save it.
        
        if decision == "approve":
            approval_entry.status = "approved"
        else:
            approval_entry.status = "rejected"
        
        approval_entry.decided_at = datetime.utcnow()
        approval_entry.decided_by = user_id
        approval_entry.decision_reason = payload.comment
        
        # Update session pending count (simple recalculation)
        pending_count = sum(1 for e in session.approvals.entries.values() if e.status == "pending")
        session.approvals.pending_count = pending_count
        
        # 4. Save Session
        await session_service.save_session(session)
        
        # 5. Check if we can execute
        if decision == "approve":
             if pending_count == 0:
                 # TRIGGER EXECUTION
                 logger.info(f"[Approval] All approvals granted for session {session_id}. Starting batch execution.")
                 
                 # Prepare batch execution (same logic as before)
                 async def run_batch_execution(sess_obj, service):
                        execution_results = []
                        try:
                            dispatcher = CentralDispatcher(org_id="default_org", workspace_id=workspace_id, request_id="batch_exec")
                            
                            # Gather all executable atoms
                            atoms_to_run = []
                            if sess_obj.checkpoint.atoms_pending:
                                for a_dict in sess_obj.checkpoint.atoms_pending:
                                    atoms_to_run.append(ExecutionAtom(**a_dict))
                            
                            # Load Approved Items
                            for entry in sess_obj.approvals.entries.values():
                                if entry.status == "approved" and entry.atom_snapshot:
                                     atom_data = entry.atom_snapshot.copy()
                                     if "id" in atom_data and isinstance(atom_data["id"], str):
                                         try:
                                             atom_data["id"] = UUID(atom_data["id"])
                                         except ValueError:
                                             logger.warning(f"Invalid UUID in atom_snapshot for approval {entry.approval_id}: {atom_data['id']}")
                                             atom_data["id"] = UUID(str(uuid4()))
                                     elif "id" not in atom_data:
                                         atom_data["id"] = UUID(str(uuid4()))

                                     # Merge context
                                     orig_ctx = atom_data.get("context_data", {})
                                     merged_ctx = {
                                         **orig_ctx,
                                         "workspace_id": workspace_id,
                                         "user_id": user_id,
                                         "session_id": sess_obj.session_id,
                                         "source": "batch_execution"
                                     }
                                     atom_data["context_data"] = merged_ctx

                                     atoms_to_run.append(ExecutionAtom(**atom_data))

                            # Execute each atom
                            for atom in atoms_to_run:
                                atom_result = {
                                    "atom_id": str(atom.id),
                                    "tool_name": atom.tool_name,
                                    "description": atom.description,
                                }
                                try:
                                    logger.info(f"[Execution] Running atom {atom.id} ({atom.tool_name})")
                                    phase4_result = await dispatcher.dispatch(atom)
                                    
                                    atom_result["status"] = phase4_result.status
                                    atom_result["output"] = phase4_result.output if phase4_result.output else None
                                    atom_result["error"] = phase4_result.error if hasattr(phase4_result, 'error') and phase4_result.error else None
                                    
                                    # Update session with executed atom
                                    if sess_obj.checkpoint.atoms_executed is None:
                                        sess_obj.checkpoint.atoms_executed = []
                                    
                                    exec_dump = atom.model_dump()
                                    exec_dump["status"] = phase4_result.status
                                    exec_dump["execution_result"] = str(phase4_result.output) if phase4_result.output else "No output"
                                    sess_obj.checkpoint.atoms_executed.append(exec_dump)

                                except Exception as e:
                                    logger.error(f"Atom execution failed: {e}", exc_info=True)
                                    atom_result["status"] = "FAILED"
                                    atom_result["output"] = None
                                    atom_result["error"] = str(e)
                                    
                                    if sess_obj.checkpoint.atoms_executed is None:
                                        sess_obj.checkpoint.atoms_executed = []
                                    
                                    exec_dump = atom.model_dump()
                                    exec_dump["status"] = "FAILED"
                                    exec_dump["execution_result"] = str(e)
                                    sess_obj.checkpoint.atoms_executed.append(exec_dump)
                                
                                execution_results.append(atom_result)
                                    
                            # Update Session to Completed
                            sess_obj.status = SessionStatus.COMPLETED
                            sess_obj.checkpoint.current_node = "phase4_execution"
                            sess_obj.checkpoint.atoms_pending = []
                            
                            await service.save_session(sess_obj)
                            logger.info(f"[Execution] Batch execution complete. Session {sess_obj.session_id} finished.")
                            
                        except Exception as e:
                            logger.error(f"Batch execution failed: {e}", exc_info=True)
                        
                        return execution_results

                 # Run synchronously and get results
                 execution_results = await run_batch_execution(session, session_service)
                 
                 # Build detailed response
                 success_count = sum(1 for r in execution_results if r.get("status") == "SUCCESS")
                 failed_count = sum(1 for r in execution_results if r.get("status") == "FAILED")
                 
                 return JSONResponse(content=jsonable_encoder({
                    "status": "success",
                    "message": f"All approvals granted. Executed {len(execution_results)} atom(s): {success_count} succeeded, {failed_count} failed.",
                    "approval_id": approval_id,
                    "session_id": session_id,
                    "execution_summary": {
                        "total_executed": len(execution_results),
                        "succeeded": success_count,
                        "failed": failed_count,
                    },
                    "execution_results": execution_results,
                    "remaining_approvals": [],
                 }))
             else:
                 # Still waiting for other approvals — show what's remaining
                 remaining = [
                     {
                         "approval_id": entry.approval_id,
                         "atom_id": entry.atom_id,
                         "skill": entry.atom_snapshot.get("tool_name", "unknown") if entry.atom_snapshot else "unknown",
                         "description": entry.atom_snapshot.get("description", "") if entry.atom_snapshot else "",
                         "status": entry.status,
                     }
                     for entry in session.approvals.entries.values()
                     if entry.status == "pending"
                 ]
                 return JSONResponse(content=jsonable_encoder({
                    "status": "success",
                    "message": f"Atom approved. Waiting for {pending_count} more approval(s).",
                    "approval_id": approval_id,
                    "session_id": session_id,
                    "approved_atom": {
                        "atom_id": approval_entry.atom_id,
                        "skill": approval_entry.atom_snapshot.get("tool_name", "unknown") if approval_entry.atom_snapshot else "unknown",
                        "description": approval_entry.atom_snapshot.get("description", "") if approval_entry.atom_snapshot else "",
                    },
                    "remaining_approvals": remaining,
                 }))
        
        else:
             # Rejected — show what was rejected and what's left
             remaining = [
                 {
                     "approval_id": entry.approval_id,
                     "atom_id": entry.atom_id,
                     "skill": entry.atom_snapshot.get("tool_name", "unknown") if entry.atom_snapshot else "unknown",
                     "description": entry.atom_snapshot.get("description", "") if entry.atom_snapshot else "",
                     "status": entry.status,
                 }
                 for entry in session.approvals.entries.values()
                 if entry.status == "pending"
             ]
             return JSONResponse(content=jsonable_encoder({
                "status": "rejected",
                "message": "Atom rejected.",
                "approval_id": approval_id,
                "session_id": session_id,
                "rejected_atom": {
                    "atom_id": approval_entry.atom_id,
                    "skill": approval_entry.atom_snapshot.get("tool_name", "unknown") if approval_entry.atom_snapshot else "unknown",
                    "description": approval_entry.atom_snapshot.get("description", "") if approval_entry.atom_snapshot else "",
                },
                "remaining_approvals": remaining,
                "comment": payload.comment,
             }))

    except Exception as e:
        logger.error(f"Approval error: {e}", exc_info=True)
        return JSONResponse(content=jsonable_encoder({
            "status": "error",
            "error": str(e),
            "approval_id": payload.approval_id,
        }))

