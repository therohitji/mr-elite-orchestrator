# app/routers/phase4.py
"""
Phase 4 Router: Execution Layer API Endpoints

Provides:
1. Reusable `run_phase4_pipeline()` function for main.py integration
2. Isolated endpoints to test Phase 4 components

Core Function:
- run_phase4_pipeline: Executes approved atoms via CentralDispatcher

Test Endpoints:
- POST /phase4/test - Full Phase 4 with debug output
- POST /phase4/dispatch - Single atom dispatch
- GET /phase4/agents - List available agents
- GET /phase4/status - Health check
"""

import asyncio
import logging
from typing import Optional, List, Dict, Any, Callable
from uuid import UUID
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

# Import shared schemas and utilities
from app.routers.shared import AtomInput, serialize_uuid, atom_input_to_execution_atom

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/phase4", tags=["Phase 4 - Execution"])


# ─────────────────── PHASE 4 RESULT DATACLASS ──────────────────

@dataclass
class Phase4Result:
    """Result from running Phase 4 pipeline"""
    # Status
    success: bool
    
    # Executed atoms
    executed_atoms: List[Any] = field(default_factory=list)
    
    # Atoms that need clarification
    clarification_atoms: List[Any] = field(default_factory=list)
    
    # Atoms added from re-planning
    replan_atoms: List[Any] = field(default_factory=list)
    
    # Dispatcher results
    dispatcher_results: List[Any] = field(default_factory=list)  # DispatcherResult objects
    execution_results: List[Dict[str, Any]] = field(default_factory=list)  # Dict form
    
    # Atom outputs for dependency chaining
    atom_outputs: Dict[str, Any] = field(default_factory=dict)
    
    # Formatted response (for clarification handling)
    formatted_response: Optional[Dict[str, Any]] = None
    
    # Clarification ID (if created)
    clarification_id: Optional[str] = None
    
    # Summary counts
    success_count: int = 0
    failed_count: int = 0
    timeout_count: int = 0
    clarification_count: int = 0
    skipped_count: int = 0
    replan_count: int = 0
    
    # Total execution time
    total_duration_ms: float = 0.0
    
    # Error info
    error: Optional[str] = None
    error_type: Optional[str] = None


# ─────────────────── CORE PIPELINE FUNCTION ──────────────────

async def run_phase4_pipeline(
    atoms: List[Any],  # List[ExecutionAtom]
    org_id: str = "default_org",
    workspace_id: str = "default",
    request_id: Optional[str] = None,
    original_message: Optional[str] = None,  # Original user message for re-planning
    clarification_handler: Optional[Callable] = None,  # Callback for clarification
    replan_callback: Optional[Callable] = None,  # Callback for re-planning
    memory_callback: Optional[Callable] = None,  # Callback for memory storage
) -> Phase4Result:
    """
    Execute the complete Phase 4 pipeline.
    
    This is the core function that main.py calls. It returns a Phase4Result
    containing execution results for all approved atoms.
    
    Per PRD Section 6 - Phase 4 is the Central Dispatcher that:
      1. Routes atoms to correct agent handlers
      2. Handles dependency injection between atoms
      3. Manages clarification requests
      4. Invokes re-planning when atoms require it
      5. Stores results for chaining
    
    Args:
        atoms: List of approved ExecutionAtom from Phase 3
        org_id: Organization ID for multi-tenancy
        workspace_id: Workspace ID for context
        request_id: Optional request ID for tracing
        original_message: Original user message (for re-planning context)
        clarification_handler: Optional callback(atom, prompt) -> clarification_id
        replan_callback: Optional callback(atom, result, message) -> List[new_atoms]
        memory_callback: Optional callback(atom, result) -> None
        
    Returns:
        Phase4Result with execution results and outputs
    """
    from app.phase4.dispatcher import CentralDispatcher, ExecutionStatus
    from uuid import uuid4
    
    try:
        # Initialize Phase 4 dispatcher
        dispatcher = CentralDispatcher(
            org_id=org_id,
            workspace_id=workspace_id,
            request_id=request_id or str(uuid4()),
        )
        
        # Track results
        executed_atoms = []
        clarification_atoms = []
        replan_atoms = []
        dispatcher_results = []
        execution_results = []
        atom_outputs = {}
        formatted_response = None
        clarification_id = None
        
        # Counters
        success_count = 0
        failed_count = 0
        timeout_count = 0
        clarification_count = 0
        skipped_count = 0
        replan_count = 0
        total_duration_ms = 0.0
        
        # Use a list we can extend during iteration (for re-planning)
        atom_queue = list(atoms)
        
        # Process each atom through Phase 4 (use index-based loop for dynamic extension)
        i = 0
        while i < len(atom_queue):
            atom = atom_queue[i]
            i += 1
            try:
                # Skip atoms awaiting approval
                if atom.status == "PAUSED":
                    logger.info(f"[Phase4] Skipping atom {atom.id} - awaiting approval")
                    skipped_count += 1
                    continue
                
                # Inject previous atom outputs into context_data for dependent atoms
                if atom.dependencies:
                    for dep_id in atom.dependencies:
                        dep_id_str = str(dep_id)
                        if dep_id_str in atom_outputs:
                            atom.context_data["previous_output"] = atom_outputs[dep_id_str]
                            logger.debug(f"[Phase4] Injected output from {dep_id_str} into {atom.id}")
                
                # Dispatch the atom
                logger.info(f"[Phase4] Executing atom {atom.id}...")
                result = await dispatcher.dispatch(atom)
                
                # Update atom with result
                atom.status = result.status.value
                atom.execution_result = result.to_dict()
                atom.completed_at = asyncio.get_event_loop().time()
                
                # Track outputs for dependent atoms
                if result.status == ExecutionStatus.SUCCESS:
                    atom_outputs[str(atom.id)] = result.output
                    success_count += 1
                elif result.status == ExecutionStatus.FAILED:
                    failed_count += 1
                elif result.status == ExecutionStatus.TIMEOUT:
                    timeout_count += 1
                
                total_duration_ms += result.duration_ms
                
                dispatcher_results.append(result)
                execution_results.append(result.to_dict())
                executed_atoms.append(atom)
                
                logger.info(
                    f"[Phase4] Atom {atom.id} -> {result.status.value} "
                    f"({result.duration_ms:.2f}ms)"
                )
                
                # Call memory callback if provided and successful
                if memory_callback and result.status == ExecutionStatus.SUCCESS:
                    try:
                        await memory_callback(atom, result)
                    except Exception as mem_err:
                        logger.warning(f"[Phase4] Memory callback error: {mem_err}")
                
                # Handle re-planning if required
                if (
                    replan_callback
                    and hasattr(atom, 'requires_replan')
                    and atom.requires_replan
                    and result.status == ExecutionStatus.SUCCESS
                ):
                    logger.info(f"[Phase4] Re-planning for atom {atom.id}")
                    try:
                        new_atoms = await replan_callback(
                            atom, result, original_message or ""
                        )
                        if new_atoms:
                            replan_atoms.extend(new_atoms)
                            atom_queue.extend(new_atoms)  # Add to queue for execution
                            replan_count += len(new_atoms)
                            logger.info(f"[Phase4] Added {len(new_atoms)} atoms from re-planning")
                    except Exception as rp_err:
                        logger.warning(f"[Phase4] Replan callback error: {rp_err}")
                
            except Exception as atom_error:
                logger.error(f"[Phase4] Failed to execute atom {atom.id}: {atom_error}")
                execution_results.append({
                    "atom_id": str(atom.id),
                    "status": "FAILED",
                    "error": str(atom_error),
                })
                failed_count += 1
                executed_atoms.append(atom)
        
        logger.info(
            f"[Phase4] Pipeline complete: {len(executed_atoms)} atoms executed "
            f"(success={success_count}, failed={failed_count}, clarification={clarification_count}, replan={replan_count})"
        )
        
        return Phase4Result(
            success=True,
            executed_atoms=executed_atoms,
            clarification_atoms=clarification_atoms,
            replan_atoms=replan_atoms,
            dispatcher_results=dispatcher_results,
            execution_results=execution_results,
            atom_outputs=atom_outputs,
            formatted_response=formatted_response,
            clarification_id=clarification_id,
            success_count=success_count,
            failed_count=failed_count,
            timeout_count=timeout_count,
            clarification_count=clarification_count,
            skipped_count=skipped_count,
            replan_count=replan_count,
            total_duration_ms=total_duration_ms,
        )
        
    except Exception as e:
        logger.error(f"Phase 4 pipeline error: {e}", exc_info=True)
        return Phase4Result(
            success=False,
            error=str(e),
            error_type=type(e).__name__,
        )


# ─────────────────── REQUEST SCHEMAS ──────────────────
# Note: AtomInput is imported from app.routers.shared


class Phase4Request(BaseModel):
    """Request payload for Phase 4 testing"""
    atoms: List[AtomInput]
    org_id: str = "default_org"
    workspace_id: str = "default"
    request_id: Optional[str] = None


class DispatchSingleRequest(BaseModel):
    """Request for single atom dispatch"""
    atom: AtomInput
    org_id: str = "default_org"
    workspace_id: str = "default"


# ─────────────────── ALIASED HELPERS ──────────────────
# These are aliased from shared module for backward compatibility
_serialize_uuid = serialize_uuid
_atom_to_execution_atom = atom_input_to_execution_atom


# ─────────────────── ENDPOINTS ──────────────────

@router.post("/test")
async def test_phase4_complete(request: Phase4Request):
    """
    🧪 TEST PHASE 4 COMPLETE
    
    Runs the full Phase 4 pipeline in isolation using run_phase4_pipeline().
    Returns all execution results for debugging.
    
    **Use this to verify:**
    - Atoms are dispatched to correct agents
    - Dependency injection is working
    - Execution results are captured correctly
    """
    try:
        # Convert input atoms to ExecutionAtom objects
        atoms = [_atom_to_execution_atom(a) for a in request.atoms]
        
        # Run the pipeline
        result = await run_phase4_pipeline(
            atoms=atoms,
            org_id=request.org_id,
            workspace_id=request.workspace_id,
            request_id=request.request_id,
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
                "executed": len(result.executed_atoms),
                "success": result.success_count,
                "failed": result.failed_count,
                "timeout": result.timeout_count,
                "clarification": result.clarification_count,
                "skipped": result.skipped_count,
                "total_duration_ms": round(result.total_duration_ms, 2),
            },
        }
        
        # Add executed atom details
        response["executed_atoms"] = [
            {
                "atom_id": str(atom.id),
                "tool_name": atom.tool_name,
                "target_agent": atom.target_agent,
                "description": atom.description,
                "status": atom.status,
            }
            for atom in result.executed_atoms
        ]
        
        # Add execution results
        response["execution_results"] = [_serialize_uuid(r) for r in result.execution_results]
        
        # Add atom outputs (for dependency checking)
        response["atom_outputs"] = _serialize_uuid(result.atom_outputs)
        
        # Add formatted response if present
        if result.formatted_response:
            response["formatted_response"] = result.formatted_response
        
        # Add error info if present
        if result.error:
            response["error"] = result.error
            response["error_type"] = result.error_type
        
        return JSONResponse(content=jsonable_encoder(response))
        
    except Exception as e:
        logger.error(f"Phase 4 test failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=jsonable_encoder({
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            })
        )


@router.post("/dispatch")
async def dispatch_single_atom(request: DispatchSingleRequest):
    """
    🚀 DISPATCH SINGLE ATOM
    
    Execute a single atom through the Phase 4 dispatcher.
    Shows full execution trace and result.
    
    Useful for debugging individual atom execution.
    """
    try:
        from app.phase4.dispatcher import CentralDispatcher
        from uuid import uuid4
        
        # Convert to ExecutionAtom
        atom = _atom_to_execution_atom(request.atom)
        
        # Create dispatcher
        dispatcher = CentralDispatcher(
            org_id=request.org_id,
            workspace_id=request.workspace_id,
            request_id=str(uuid4()),
        )
        
        # Dispatch
        result = await dispatcher.dispatch(atom)
        
        return {
            "atom_id": str(atom.id),
            "tool_name": atom.tool_name,
            "target_agent": atom.target_agent,
            "description": atom.description,
            "execution": {
                "status": result.status.value,
                "output": _serialize_uuid(result.output),
                "error": result.error,
                "duration_ms": round(result.duration_ms, 2),
                "trace_id": result.trace_id,
                "retryable": result.retryable,
            },
        }
        
    except Exception as e:
        logger.error(f"Dispatch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents")
async def list_available_agents():
    """
    📦 LIST AVAILABLE AGENTS
    
    Show all agent handlers that Phase 4 can dispatch to.
    
    Useful for understanding routing options.
    """
    try:
        # List known agent handlers
        agents = [
            {
                "name": "email_agent",
                "module": "app.agents.email_agent",
                "skills": ["send_email", "create_draft", "list_emails", "search_emails", "summarize_emails"],
            },
            {
                "name": "calendar_agent",
                "module": "app.agents.calendar_agent",
                "skills": ["create_event", "update_event", "delete_event", "list_events", "get_daily_meetings"],
            },
            {
                "name": "summarizer_agent",
                "module": "app.agents.summarizer_agent",
                "skills": ["summarize_text", "summarize_emails"],
            },
        ]
        
        return {
            "status": "success",
            "agent_count": len(agents),
            "agents": agents,
        }
        
    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
        }


@router.get("/status")
async def phase4_status():
    """
    📊 PHASE 4 STATUS
    
    Check Phase 4 dispatcher status and configuration.
    """
    try:
        from app.phase4.dispatcher import CentralDispatcher, EXECUTION_TIMEOUT_SECONDS
        
        return {
            "status": "healthy",
            "dispatcher_initialized": True,
            "configuration": {
                "execution_timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
                "retry_enabled": True,
            },
            "description": "Phase 4 Execution Layer - Central Dispatcher for atom routing and execution",
        }
        
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }
