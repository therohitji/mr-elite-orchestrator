# app/routers/phase2.py
"""
Phase 2 Router: Reasoning/Enrichment Layer API Endpoints

Provides:
1. Reusable `run_phase2_pipeline()` function for main.py integration
2. Isolated endpoints to test Phase 2 components

Core Function:
- run_phase2_pipeline: Executes full Phase 2 (policy + resources + memory + state enrichment)

Test Endpoints:
- POST /phase2/test - Full Phase 2 with debug output
- POST /phase2/enrich - Single atom enrichment
- POST /phase2/policy - Policy check only
"""

import logging
from typing import Optional, List, Dict, Any
from uuid import UUID
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

# Import shared schemas and utilities
from app.routers.shared import AtomInput, serialize_uuid, atom_input_to_execution_atom

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/phase2", tags=["Phase 2 - Reasoning"])


# ─────────────────── PHASE 2 RESULT DATACLASS ──────────────────

@dataclass
class Phase2Result:
    """Result from running Phase 2 pipeline"""
    # Status
    success: bool
    
    # Enriched atoms (atoms with updated context_data)
    enriched_atoms: List[Any] = field(default_factory=list)
    
    # Evaluation results per atom
    evaluation_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # Clarification atoms (atoms that need user input before proceeding)
    # These are detected early to allow short-circuit before Phase 3
    clarification_atoms: List[Any] = field(default_factory=list)
    
    # Summary counts
    ready_count: int = 0
    requires_approval_count: int = 0
    blocked_count: int = 0
    clarification_count: int = 0  # Count of clarification atoms
    
    # Error info
    error: Optional[str] = None
    error_type: Optional[str] = None


# ─────────────────── CORE PIPELINE FUNCTION ──────────────────

async def run_phase2_pipeline(
    atoms: List[Any],  # List[ExecutionAtom]
    workspace_id: str = "default",
    chat_history: Optional[List[Dict[str, Any]]] = None,
    thread_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Phase2Result:
    """
    Execute the complete Phase 2 pipeline.
    
    This is the core function that main.py calls. It returns a Phase2Result
    containing enriched atoms with policy, resources, memory, and state context.
    
    Per PRD - Phase 2 runs two parallel threads:
      - Thread A: LOGIC (Policy + Resources)
      - Thread B: CONTEXT (Memory + State)
    
    Args:
        atoms: List of ExecutionAtom from Phase 1
        workspace_id: Workspace ID for Supabase lookups
        chat_history: Optional chat history for context injection
        thread_id: Optional thread ID for context
        session_id: Optional session ID for context
        
    Returns:
        Phase2Result with enriched atoms and evaluation summaries
    """
    from app.phase2.evaluator import Phase2Evaluator
    
    try:
        # Initialize Phase 2 evaluator
        evaluator = Phase2Evaluator()
        
        # Track results
        enriched_atoms = []
        evaluation_results = []
        ready_count = 0
        requires_approval_count = 0
        blocked_count = 0
        
        # Inject chat context into atoms before evaluation
        if chat_history:
            for atom in atoms:
                atom.context_data["chat_history"] = chat_history
                if thread_id:
                    atom.context_data["thread_id"] = thread_id
                if session_id:
                    atom.context_data["session_id"] = session_id
        
        # Process each atom through Phase 2
        for atom in atoms:
            try:
                result = await evaluator.evaluate_atom_async(
                    atom, 
                    workspace_id=workspace_id
                )
                
                # Update atom with enriched context from result
                if isinstance(result, dict) and "context_data" in result:
                    atom.context_data.update(result.get("context_data", {}))
                
                # Track status counts
                status = result.get("status", "ready")
                if status == "ready":
                    ready_count += 1
                elif status == "requires_approval":
                    requires_approval_count += 1
                elif status == "blocked":
                    blocked_count += 1
                
                enriched_atoms.append(atom)
                evaluation_results.append(result)
                
                logger.debug(
                    f"[Phase2] Atom {atom.id} enriched: decision={result.get('decision')}, "
                    f"status={status}"
                )
                
            except Exception as atom_error:
                logger.error(f"[Phase2] Failed to evaluate atom {atom.id}: {atom_error}")
                # Still include atom but mark evaluation as failed
                evaluation_results.append({
                    "atom_id": str(atom.id),
                    "status": "error",
                    "error": str(atom_error),
                })
                enriched_atoms.append(atom)
        
        logger.info(
            f"[Phase2] Pipeline complete: {len(enriched_atoms)} atoms enriched "
            f"(ready={ready_count}, approval={requires_approval_count}, blocked={blocked_count})"
        )
        
        # ─────────── EARLY DETECTION: CLARIFICATION ATOMS ─────────
        # Detect atoms that require user input (clarification) BEFORE Phase 3
        # This allows main.py to short-circuit and not proceed to Phase 3/4
        clarification_atoms = [
            atom for atom in enriched_atoms
            if atom.target_agent == "HUMAN_USER"
            and atom.tool_name in ("user_input_required", "human_input")
        ]
        
        if clarification_atoms:
            logger.info(
                f"[Phase2] ⚠️ Early detection: {len(clarification_atoms)} clarification atom(s) found - "
                f"main.py should short-circuit before Phase 3"
            )
        
        return Phase2Result(
            success=True,
            enriched_atoms=enriched_atoms,
            evaluation_results=evaluation_results,
            clarification_atoms=clarification_atoms,
            ready_count=ready_count,
            requires_approval_count=requires_approval_count,
            blocked_count=blocked_count,
            clarification_count=len(clarification_atoms),
        )
        
    except Exception as e:
        logger.error(f"Phase 2 pipeline error: {e}", exc_info=True)
        return Phase2Result(
            success=False,
            error=str(e),
            error_type=type(e).__name__,
        )


# ─────────────────── REQUEST SCHEMAS ──────────────────
# Note: AtomInput is imported from app.routers.shared


class Phase2Request(BaseModel):
    """Request payload for Phase 2 testing"""
    atoms: List[AtomInput]
    workspace_id: str = "1f2511ac-bd16-46e1-91b5-eb8901a6731e"
    chat_history: Optional[List[Dict[str, str]]] = None
    thread_id: Optional[str] = None
    session_id: Optional[str] = None


class EnrichSingleRequest(BaseModel):
    """Request for single atom enrichment"""
    atom: AtomInput
    workspace_id: str = "default"


class PolicyCheckRequest(BaseModel):
    """Request for policy check only"""
    action_description: str
    tool_name: str
    risk_level: str = "LOW"
    workspace_id: str = "default"


# ─────────────────── ALIASED HELPERS ──────────────────
# These are aliased from shared module for backward compatibility
_serialize_uuid = serialize_uuid
_atom_to_execution_atom = atom_input_to_execution_atom


# ─────────────────── ENDPOINTS ──────────────────

@router.post("/test")
async def test_phase2_complete(request: Phase2Request):
    """
    🧪 TEST PHASE 2 COMPLETE
    
    Runs the full Phase 2 pipeline in isolation using run_phase2_pipeline().
    Returns all intermediate results for debugging.
    
    **Use this to verify:**
    - Policy evaluation is working correctly
    - Resource constraints are applied
    - Memory context is being injected
    - State checks are performing correctly
    """
    try:
        # Convert input atoms to ExecutionAtom objects
        atoms = [_atom_to_execution_atom(a) for a in request.atoms]
        
        # Run the pipeline
        result = await run_phase2_pipeline(
            atoms=atoms,
            workspace_id=request.workspace_id,
            chat_history=request.chat_history,
            thread_id=request.thread_id,
            session_id=request.session_id,
        )
        
        # Build detailed response
        response = {
            "request": {
                "atom_count": len(request.atoms),
                "workspace_id": request.workspace_id,
            },
            "status": "success" if result.success else "error",
            "summary": {
                "total_atoms": len(result.enriched_atoms),
                "ready": result.ready_count,
                "requires_approval": result.requires_approval_count,
                "blocked": result.blocked_count,
            },
        }
        
        # Add enriched atom details
        enriched_details = []
        for i, atom in enumerate(result.enriched_atoms):
            eval_result = result.evaluation_results[i] if i < len(result.evaluation_results) else {}
            enriched_details.append({
                "atom_id": str(atom.id),
                "tool_name": atom.tool_name,
                "target_agent": atom.target_agent,
                "description": atom.description,
                "status": atom.status,
                "decision": eval_result.get("decision", "N/A"),
                "context_enrichment": {
                    "has_policy": "policy" in atom.context_data,
                    "has_resources": "resources" in atom.context_data,
                    "has_memory": "memory" in atom.context_data,
                    "has_state": "state" in atom.context_data,
                },
                "context_data": _serialize_uuid(atom.context_data),
            })
        
        response["enriched_atoms"] = enriched_details
        response["evaluation_results"] = [_serialize_uuid(r) for r in result.evaluation_results]
        
        # Add error info if present
        if result.error:
            response["error"] = result.error
            response["error_type"] = result.error_type
        
        return JSONResponse(content=jsonable_encoder(response))
        
    except Exception as e:
        logger.error(f"Phase 2 test failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=jsonable_encoder({
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            })
        )


@router.post("/enrich")
async def enrich_single_atom(request: EnrichSingleRequest):
    """
    🔍 ENRICH SINGLE ATOM
    
    Run Phase 2 enrichment on a single atom.
    Shows full policy/resources/memory/state context additions.
    
    Useful for debugging individual atom processing.
    """
    try:
        from app.phase2.evaluator import Phase2Evaluator
        
        # Convert to ExecutionAtom
        atom = _atom_to_execution_atom(request.atom)
        
        # Run evaluation
        evaluator = Phase2Evaluator()
        result = await evaluator.evaluate_atom_async(atom, workspace_id=request.workspace_id)
        
        return {
            "atom_id": str(atom.id),
            "tool_name": atom.tool_name,
            "original_context": _serialize_uuid(request.atom.context_data),
            "enriched_context": _serialize_uuid(atom.context_data),
            "evaluation": {
                "decision": result.get("decision"),
                "status": result.get("status"),
            },
            "policy_result": result.get("context_data", {}).get("policy", {}),
            "resources_result": result.get("context_data", {}).get("resources", {}),
            "memory_result": result.get("context_data", {}).get("memory", {}),
            "state_result": result.get("context_data", {}).get("state", {}),
        }
        
    except Exception as e:
        logger.error(f"Enrichment failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/policy")
async def check_policy_only(request: PolicyCheckRequest):
    """
    📋 POLICY CHECK ONLY
    
    Run just the policy evaluation component.
    Shows how policy rules would apply to an action.
    
    Useful for testing policy configurations.
    """
    try:
        from app.phase2.policy.policy_engine import PolicyEngine
        
        # Create a minimal context for policy checking
        check_context = {
            "action": request.action_description,
            "tool_name": request.tool_name,
            "risk_level": request.risk_level,
        }
        
        # Run policy check
        engine = PolicyEngine()
        policy_result = await engine.evaluate(
            action_description=request.action_description,
            tool_name=request.tool_name,
            risk_level=request.risk_level,
            context=check_context,
        )
        
        return {
            "action": request.action_description,
            "tool_name": request.tool_name,
            "risk_level": request.risk_level,
            "policy_result": policy_result,
        }
        
    except Exception as e:
        logger.error(f"Policy check failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def phase2_status():
    """
    📊 PHASE 2 STATUS
    
    Check Phase 2 engine status and configuration.
    """
    try:
        from app.phase2.evaluator import Phase2Evaluator
        
        evaluator = Phase2Evaluator()
        
        return {
            "status": "healthy",
            "evaluator_initialized": evaluator is not None,
            "components": {
                "policy_engine": True,
                "resource_engine": True,
                "memory_engine": True,
                "state_engine": True,
            },
            "description": "Phase 2 Reasoning Layer - Policy + Resources + Memory + State enrichment",
        }
        
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }
