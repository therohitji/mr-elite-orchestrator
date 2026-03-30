"""
MR ELITE: AUTONOMOUS ORCHESTRATION ENGINE (5-PHASE PIPELINE)

Per PRD: Decision First → Validate → Control/Secure → Execute → Evaluate/Learn

Phases:
  1. Planning: Pre-flight → Classify → CoT → Atomize
  2. Reasoning: Parallel Logic (Policy+Resources) + Context (Memory+State)
  3. Control: Privacy/Security/Reliability Guards + Approval Gate
  4. Execution: Central Dispatcher (CODE_TOOL / AI_AGENT / WORKFLOW)
  5. Evaluation: Analytics + Learning Loop

This file wires everything together.
"""

import asyncio
import logging
import sys
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from uuid import UUID
import json
from datetime import datetime, timezone

# Phase imports (Phase 1 logic is now in run_phase1_pipeline)
from app.phase1.atoms import ExecutionAtom

from app.phase4.dispatcher import CentralDispatcher, ExecutionStatus, DispatcherResult
from app.phase4.response_formatter import ResponseFormatter
from app.phase5.evaluator import EvaluationOrchestrator
from app.core.supabase_client import supabase_client
from app.core.gemini_client import call_gemini, call_gemini_text
from app.core.clarification_manager import (
    ClarificationManager,
    ClarificationSession,
    detect_domain_from_atom,
    detect_missing_fields_for_domain,
)
from app.core.session import (
    SessionService,
    SessionState,
    SessionStatus,
    ClarificationStatus,
    get_session_service,
)
from app.core.thread_history import get_thread_history_service

# Import phase routers for modular testing
from app.routers.phase1 import router as phase1_router, run_phase1_pipeline, Phase1Result
from app.routers.phase2 import router as phase2_router, run_phase2_pipeline, Phase2Result
from app.routers.phase3 import router as phase3_router, run_phase3_pipeline, Phase3Result
from app.routers.phase4 import router as phase4_router, run_phase4_pipeline, Phase4Result
from app.routers.ingestion import router as ingestion_router

app = FastAPI(
    title="Mr Elite – Autonomous Orchestration Engine",
    description="Decision-aware execution layer with policy + control + learning",
    version="1.0.0",
)

# Register phase routers for isolated testing
app.include_router(phase1_router)
app.include_router(phase2_router)
app.include_router(phase3_router)
app.include_router(phase4_router)
app.include_router(ingestion_router)


def convert_uuids_to_strings(obj):
    """Recursively convert UUID objects to strings."""
    if isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: convert_uuids_to_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_uuids_to_strings(item) for item in obj]
    return obj


# ─────────────────── HELPER FUNCTION ──────────────────


# ─────────────────── PAYLOAD SCHEMAS ──────────────────


class ContextMessage(BaseModel):
    """Single message in chat history from Node.js"""

    role: str  # user | assistant | tool | system | api
    content: str
    content_type: str = "text"  # text | tool_result | system_note | error


class InputPayload(BaseModel):
    """Request payload from Node.js middleware"""

    # Identity (Required)
    thread_id: str
    session_id: str
    org_id: str

    # Identity (Optional)
    workspace_id: str = ""
    user_id: Optional[str] = None

    # Message (Required)
    message: str

    # Context - Chat history array from Node.js (Optional)
    context: Optional[List[ContextMessage]] = None

    # Extensibility (Optional)
    metadata: Optional[Dict[str, Any]] = None


class PolicyPayload(BaseModel):
    rule_text: str
    policy_type: str


def _determine_missing_fields(atom: ExecutionAtom) -> List[str]:
    """Return fields that still need values for clarification."""
    missing = []
    for key, value in atom.tool_arguments.items():
        if value in (None, "", [], {}, [""]):
            missing.append(key)
    return missing or ["details"]


def _serialize_atom_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively convert payload into JSON serializable form."""

    def _convert(value):
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_convert(v) for v in value]
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    return _convert(payload)


def _build_suggested_prompt(field_name: str) -> str:
    pretty_name = field_name.replace("_", " ").strip()
    return f"Please provide the {pretty_name} so I can continue."


async def _save_clarification_request(
    atom: ExecutionAtom, prompt: str, missing_fields: List[str], user_id: Optional[str]
) -> str:
    """Persist clarification atom into approval_queue as a pending record."""
    snapshot = atom.model_dump()
    snapshot["clarification_meta"] = {
        "missing_fields": missing_fields,
        "last_prompt": prompt,
    }

    request = await phase3.approval_service.create(
        atom_payload=snapshot,
        risk_level="MEDIUM",
        risk_reason="clarification_needed",
        atom_id=str(atom.id),
        agent_role=phase3.agent_role or "mr_elite",
        requested_by=user_id or "system",
    )

    return request.approval_id


async def _update_clarification_payload(
    clarification_id: str, payload: Dict[str, Any]
) -> None:
    """Update stored clarification record with new snapshot."""
    try:
        serialized = _serialize_atom_payload(payload)
        supabase = phase3.approval_service.supabase
        supabase.table("approval_queue").update(
            {
                "atom_payload": serialized,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", clarification_id).execute()
    except Exception as e:
        logger.warning(
            f"[Clarification] Failed to update record {clarification_id}: {e}"
        )


async def _handle_clarification_followup(
    payload: InputPayload, 
    clarification_id: str
) -> JSONResponse:
    """
    Handle follow-up messages that respond to an existing clarification.
    
    PERSISTENT FLOW (using SessionService):
    1. Load session from workflow_states via SessionService
    2. Get the active clarification from session state
    3. Parse user response with ClarificationManager helpers
    4. Update clarification state in session
    5. If complete: resume pipeline from checkpoint
    6. If incomplete: ask for next missing field
    """
    # Resolve session by clarification_id
    session_service = get_session_service(
        org_id=payload.org_id or "default_org",
        workspace_id=payload.workspace_id or "default",
    )
    
    resolution = await session_service.resolve_session(
        thread_id=payload.thread_id,
        session_id=payload.session_id,
        clarification_id=clarification_id,
        user_id=payload.user_id,
        message=payload.message,
    )
    
    session = resolution.session
    
    # Find the clarification entry
    entry = session.clarifications.entries.get(clarification_id)
    if not entry:
        # Fall back to legacy in-memory handling for backward compatibility
        logger.warning(f"[Clarification] ID {clarification_id} not found in persistent session, trying legacy")
        return await _process_clarification_followup_legacy(payload, clarification_id)
    
    # Parse user response using ClarificationManager helpers
    parse_result = await ClarificationManager.parse_user_response(
        user_response=payload.message,
        missing_fields=entry.missing_fields,
        collected_data=entry.collected_data,
        domain=entry.domain,
    )
    
    if parse_result.get("status") == "error":
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": parse_result.get("error", "Failed to parse response"),
                "session_id": session.session_id,
            },
        )
    
    # Update clarification with collected data
    collected_field = parse_result.get("field")
    collected_value = parse_result.get("value")
    
    if collected_field and collected_value is not None:
        result = await session_service.update_clarification(
            session=session,
            clarification_id=clarification_id,
            collected_field=collected_field,
            collected_value=collected_value,
            user_message=payload.message,
            assistant_response=parse_result.get("response", ""),
        )
    else:
        # Could not extract a field value, ask again
        ask_msg = entry.prompt or f"Please provide the required information for: {', '.join(entry.missing_fields)}"
        # Save clarification exchange to thread history
        try:
            th_svc = get_thread_history_service()
            await th_svc.save_exchange(
                thread_id=payload.thread_id,
                org_id=payload.org_id or "default_org",
                workspace_id=payload.workspace_id or "default",
                user_id=payload.user_id,
                user_message=payload.message,
                assistant_response=ask_msg,
                session_id=session.session_id,
                metadata={"intent": "clarification", "clarification_id": clarification_id, "missing_fields": entry.missing_fields},
            )
        except Exception as th_err:
            logger.warning(f"[ThreadHistory] Failed to save clarification exchange: {th_err}")
        return JSONResponse(
            status_code=200,
            content={
                "status": "clarification_needed",
                "ui_state": "clarification_needed",
                "clarification_id": clarification_id,
                "session_id": session.session_id,
                "message": ask_msg,
                "missing_fields": entry.missing_fields,
                "collected_so_far": entry.collected_data,
            },
        )
    
    # Check if clarification is complete
    if not result.is_complete:
        # Still collecting fields - generate next prompt
        next_field = entry.missing_fields[0] if entry.missing_fields else "details"
        next_prompt = ClarificationManager.get_field_prompt(entry.domain, next_field)
        
        # Save clarification exchange to thread history
        try:
            th_svc = get_thread_history_service()
            await th_svc.save_exchange(
                thread_id=payload.thread_id,
                org_id=payload.org_id or "default_org",
                workspace_id=payload.workspace_id or "default",
                user_id=payload.user_id,
                user_message=payload.message,
                assistant_response=f"Got it, {collected_field}: {collected_value}. {next_prompt}",
                session_id=session.session_id,
                metadata={"intent": "clarification", "clarification_id": clarification_id, "collected": {collected_field: str(collected_value)}, "missing_fields": entry.missing_fields},
            )
        except Exception as th_err:
            logger.warning(f"[ThreadHistory] Failed to save clarification exchange: {th_err}")
        
        return JSONResponse(
            status_code=200,
            content={
                "status": "clarification_needed",
                "ui_state": "clarification_needed",
                "clarification_id": clarification_id,
                "session_id": session.session_id,
                "message": next_prompt,
                "missing_fields": entry.missing_fields,
                "collected_so_far": result.collected_data,
            },
        )
    
    # COMPLETE - Resume pipeline from checkpoint
    logger.info(f"[CLARIFICATION] ✅ Complete! Resuming pipeline with enriched message")
    
    # Create new payload with enriched message
    new_metadata = dict(payload.metadata) if payload.metadata else {}
    new_metadata.pop("clarification_id", None)
    new_metadata["clarification_completed"] = True
    new_metadata["original_clarification_id"] = clarification_id
    new_metadata["session_id"] = session.session_id  # Pass session for continuity
    
    # Use enriched message from clarification result
    enriched_message = result.enriched_message or payload.message
    
    new_payload = InputPayload(
        thread_id=payload.thread_id,
        session_id=payload.session_id,
        org_id=payload.org_id,
        workspace_id=payload.workspace_id,
        user_id=payload.user_id,
        message=enriched_message,
        context=payload.context,
        metadata=new_metadata,
    )
    
    return await classify(new_payload)


async def _process_clarification_followup_legacy(
    payload: InputPayload, clarification_id: str
) -> JSONResponse:
    """
    LEGACY: Handle follow-up messages using in-memory ClarificationManager.
    
    This is kept for backward compatibility with sessions created before
    the persistent session system was implemented.
    """
    # Process response through ClarificationManager
    result = await ClarificationManager.process_response(
        clarification_id=clarification_id,
        user_response=payload.message,
    )
    
    if result["status"] == "error":
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": result["error"],
            },
        )
    
    if result["status"] == "pending":
        # Still collecting fields
        session = result["session"]
        # Save clarification exchange to thread history
        try:
            th_svc = get_thread_history_service()
            await th_svc.save_exchange(
                thread_id=payload.thread_id,
                org_id=payload.org_id or "default_org",
                workspace_id=payload.workspace_id or "default",
                user_id=payload.user_id,
                user_message=payload.message,
                assistant_response=result["next_prompt"],
                session_id=None,
                metadata={"intent": "clarification", "clarification_id": clarification_id, "collected_so_far": result.get("collected_so_far"), "missing_fields": result["missing_fields"]},
            )
        except Exception as th_err:
            logger.warning(f"[ThreadHistory] Failed to save legacy clarification exchange: {th_err}")
        return JSONResponse(
            status_code=200,
            content={
                "status": "clarification_needed",
                "ui_state": "clarification_needed",
                "clarification_id": clarification_id,
                "message": result["next_prompt"],
                "missing_fields": result["missing_fields"],
                "collected_so_far": result["collected_so_far"],
            },
        )
    
    # STATUS: COMPLETE - Re-run Phase 1 with enriched message
    session = result["session"]
    enriched_message = result["enriched_message"]
    
    logger.info(f"[CLARIFICATION] ✅ Legacy complete! Re-running Phase 1 with: {enriched_message}")
    
    # Create new payload with enriched message (remove clarification_id to avoid loop)
    new_metadata = dict(payload.metadata) if payload.metadata else {}
    new_metadata.pop("clarification_id", None)
    new_metadata["clarification_completed"] = True
    new_metadata["original_clarification_id"] = clarification_id
    
    # Build new payload
    new_payload = InputPayload(
        thread_id=payload.thread_id,
        session_id=payload.session_id,
        org_id=payload.org_id,
        workspace_id=payload.workspace_id,
        user_id=payload.user_id,
        message=enriched_message,
        context=payload.context,
        metadata=new_metadata,
    )
    
    # Re-run the full pipeline with enriched message
    return await classify(new_payload)




async def _create_clarification_session(
    atom: ExecutionAtom,
    payload: InputPayload,
    prompt: str,
) -> str:
    """
    Create a new clarification session for an atom that needs user input.
    
    Returns the clarification_id.
    """
    # Detect domain and missing fields
    domain = detect_domain_from_atom(atom)
    missing_fields = detect_missing_fields_for_domain(domain, atom.tool_arguments)
    
    # Create session in memory
    session = ClarificationManager.create_session(
        session_id=payload.session_id,
        original_message=payload.message,
        missing_fields=missing_fields,
        domain=domain,
        initial_prompt=prompt,
    )
    
    logger.info(
        f"[CLARIFICATION] Created session {session.clarification_id} "
        f"for domain={domain}, missing={missing_fields}"
    )
    
    return session.clarification_id


# ─────────────────── PHASE ENGINES ──────────────────
# Note: Phase 1 logic is in run_phase1_pipeline()
# Note: Phase 2 logic is in run_phase2_pipeline()
# Note: Phase 3 logic is in run_phase3_pipeline()
# Phase 3 engine still needed for approval_service reference in clarification/approval endpoints
from app.phase3 import Phase3ControlEngine
phase3 = Phase3ControlEngine(org_id="default_org", workspace_id="default_ws")
phase4_dispatcher = CentralDispatcher(
    org_id="default_org", workspace_id="default_ws", request_id="system_init"
)
phase5 = EvaluationOrchestrator(db_client=supabase_client)


# ─────────────────── STARTUP ──────────────────


@app.on_event("startup")
async def startup_vector_db():
    """Initialize Supabase vector DB on app startup."""
    logger.info("Starting Vector DB initialization...")
    try:
        from app.core.supabase_client import init_vector_db

        init_vector_db()
        logger.info("✅ Vector DB initialized successfully")
    except Exception as e:
        logger.error(f"❌ Vector DB initialization failed: {e}", exc_info=True)


# ─────────────────── HEALTH CHECK ──────────────────


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        from app.core.supabase_client import supabase_client

        is_connected = supabase_client is not None
        return {"status": "healthy", "supabase_connected": is_connected}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


# ─────────────────── TOOL CONNECTION TEST ──────────────────


@app.get("/test/tool-connection/{tool_name}/{user_id}")
async def test_tool_connection(tool_name: str, user_id: str):
    """
    Test endpoint to verify tool connection works immediately after MongoDB update.

    Usage:
    GET /test/tool-connection/send_email/user123

    Returns:
    - token_status: Whether token was found in MongoDB
    - tool_created: Whether tool was successfully created
    - timestamp: When test ran (shows fresh each time)
    """
    try:
        from app.core.tool_factory import ToolFactory
        from app.core.tool_tokens import get_tool_token
        from datetime import datetime

        logger.info(f"🧪 Testing tool connection: tool={tool_name}, user={user_id}")

        # Step 1: Check if token exists in MongoDB
        token = get_tool_token(tool_name, user_id)
        token_found = token is not None

        logger.info(f"   Token lookup: {'✅ Found' if token_found else '❌ Not found'}")

        if not token_found:
            return {
                "status": "token_not_found",
                "tool_name": tool_name,
                "user_id": user_id,
                "timestamp": datetime.utcnow().isoformat(),
                "message": "Token not in MongoDB. Connect tool first via frontend.",
            }

        # Step 2: Get fresh tool instance
        tool = await ToolFactory.get_tool(tool_name, user_id)
        tool_created = tool is not None

        logger.info(
            f"   Tool creation: {'✅ Success' if tool_created else '❌ Failed'}"
        )

        return {
            "status": "success",
            "tool_name": tool_name,
            "user_id": user_id,
            "token_found": token_found,
            "tool_created": tool_created,
            "timestamp": datetime.utcnow().isoformat(),
            "message": "✅ Tool connection working! Token fetched fresh from MongoDB.",
            "details": {
                "token_keys": list(token.keys()) if token_found else [],
                "tool_type": type(tool).__name__ if tool_created else None,
            },
        }

    except Exception as e:
        logger.error(f"❌ Tool connection test failed: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "tool_name": tool_name,
            "user_id": user_id,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat(),
        }


# ─────────────────── POLICY MANAGEMENT ──────────────────


@app.post("/policies/add")
async def add_policy(payload: PolicyPayload):
    """Add a new policy to the vector DB."""
    try:
        from app.core.supabase_client import insert_policy

        result = await insert_policy(payload.rule_text, payload.policy_type)
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────── MAIN CLASSIFICATION & EXECUTION ENDPOINT ──────────────────


@app.post("/classify")
async def classify(payload: InputPayload):
    """
    Main endpoint: Execute all 5 phases of the pipeline.

    Per PRD Section 1 & 8:
      Phase 1 (Planning): Classify → Plan → Atomize
      Phase 2 (Reasoning): Enrich with Policy/Resources/Memory/State
      Phase 3 (Control): Privacy/Security/Reliability/Approval Guards
      Phase 4 (Execution): Dispatch to tools
      Phase 5 (Evaluation): Analytics + Learning

    Returns complete execution result with all phase outputs.
    
    Session Management:
    - Sessions are persisted to workflow_states table
    - Clarifications and approvals are tracked within sessions
    - Sessions can be resumed after restart
    """
    try:
        # ─────────────────── CLARIFICATION HANDLING ──────────────────
        # Handle clarification follow-up before re-running classification
        clarification_id = (
            payload.metadata.get("clarification_id") if payload.metadata else None
        )
        if clarification_id:
            return await _handle_clarification_followup(payload, clarification_id)

        # ─────────────────── SESSION RESOLUTION ──────────────────
        # Resolve or create a persistent session for this request
        workspace_id = payload.workspace_id or "default"
        session_service = get_session_service(
            org_id=payload.org_id or "default_org",
            workspace_id=workspace_id,
        )
        
        # Check if frontend provided session/clarification IDs to resume
        frontend_session_id = payload.metadata.get("session_id") if payload.metadata else None
        clarification_id = payload.metadata.get("clarification_id") if payload.metadata else None
        
        session_resolution = await session_service.resolve_session(
            thread_id=payload.thread_id,
            session_id=frontend_session_id or payload.session_id,
            clarification_id=clarification_id,  # Now correctly extracted from metadata
            user_id=payload.user_id,
            message=payload.message,
        )
        
        session = session_resolution.session
        is_new_session = session_resolution.is_new
        
        logger.info(
            f"[Session] {'Created new' if is_new_session else 'Resumed'} session {session.session_id} "
            f"for thread {payload.thread_id}" + 
            (f" (clarification_id={clarification_id})" if clarification_id else "")
        )
        
        # ─────────────────── LOAD THREAD HISTORY ──────────────────
        # Load conversation history for context continuity
        thread_history_service = get_thread_history_service()
        thread_history_context = await thread_history_service.get_context_for_llm(
            thread_id=payload.thread_id,
            org_id=payload.org_id or "default_org",
            max_exchanges=10,
        )
        if thread_history_context:
            logger.info(f"[ThreadHistory] Loaded conversation context ({len(thread_history_context)} chars)")
        
        # Save original message to checkpoint
        session.checkpoint.original_message = payload.message
        await session_service.save_session(session)


        # ─────────────────── PHASE 1: PLANNING (via router function) ──────────────────
        
        # Build context for Phase 1
        context_list = []
        
        # Add thread history as system context (if available)
        if thread_history_context:
            context_list.append({
                "role": "system",
                "content": thread_history_context,
                "content_type": "thread_history",
            })
        
        # Add any frontend-provided context
        if payload.context:
            context_list.extend([
                {"role": m.role, "content": m.content, "content_type": m.content_type}
                for m in payload.context
            ])

        
        # Run Phase 1 pipeline
        phase1_result = await run_phase1_pipeline(
            message=payload.message,
            workspace_id=workspace_id,
            user_id=payload.user_id,
            org_id=payload.org_id or "default_org",
            context=context_list if context_list else None,
            generate_chat_response=True,
        )
        
        # Handle early exit (chat, query, error, blocked)
        if phase1_result.early_exit or not phase1_result.success:
            # ─────────────────── SAVE THREAD HISTORY (Early Exit) ──────────────────
            # Must save conversation even for chat/query intents
            try:
                early_response = phase1_result.early_exit_response or {}
                # Build rich assistant response
                assistant_msg = (
                    early_response.get("message") or 
                    early_response.get("formatted_response", {}).get("output", {}).get("response") or
                    early_response.get("reason") or
                    f"Early exit at {phase1_result.phase_reached}"
                )
                # Build rich metadata
                save_metadata = {
                    "intent": phase1_result.heuristic_result or (phase1_result.classification.intent if phase1_result.classification else None),
                    "domain": phase1_result.classification.domain if phase1_result.classification else None,
                    "risk_level": phase1_result.classification.risk_level if phase1_result.classification else None,
                    "phase_reached": phase1_result.phase_reached,
                    "early_exit": True,
                }
                await thread_history_service.save_exchange(
                    thread_id=payload.thread_id,
                    org_id=payload.org_id or "default_org",
                    workspace_id=workspace_id,
                    user_id=payload.user_id,
                    user_message=payload.message,
                    assistant_response=assistant_msg[:4000],
                    session_id=session.session_id,
                    metadata=save_metadata,
                )
            except Exception as th_err:
                logger.warning(f"[ThreadHistory] Failed to save early exit exchange: {th_err}")
            
            if phase1_result.early_exit_response:
                return JSONResponse(content=jsonable_encoder(phase1_result.early_exit_response))
            else:
                # Error case
                return JSONResponse(
                    content=jsonable_encoder({
                        "phase": phase1_result.phase_reached,
                        "error": phase1_result.error,
                        "error_type": phase1_result.error_type,
                        "execution_end": True,
                    })
                )
        
        # Extract results from Phase 1
        atoms = phase1_result.atoms or []
        
        # Inject session_id into all atoms for context tracking
        for atom in atoms:
            if atom.context_data is None:
                atom.context_data = {}
            atom.context_data["session_id"] = session.session_id
            
        plan = phase1_result.plan
        classification = phase1_result.classification
        
        # Build phase1_debug for final response
        phase1_debug = {
            "classification": {
                "intent": classification.intent if classification else None,
                "complexity_score": classification.complexity_score if classification else None,
                "domain": classification.domain if classification else None,
                "risk_level": classification.risk_level if classification else None,
            },
            "plan": {
                "total_steps": phase1_result.atomization_debug.get("total_steps", 0) if phase1_result.atomization_debug else 0,
                "steps": [s.description for s in plan.plan] if plan else [],
            },
            "atomization": phase1_result.atomization_debug or {},
            "all_atoms": [
                {
                    "id": str(atom.id),
                    "tool_name": atom.tool_name,
                    "target_agent": atom.target_agent,
                    "description": atom.description,
                    "risk_level": atom.risk_level,
                    "tool_arguments": atom.tool_arguments,
                    "status": "matched",
                }
                for atom in atoms
            ],
        }


        # ─────────────────── PHASE 2: REASONING (via router function) ──────────────────

        # Build chat history for context injection
        chat_history = []  # Initialize with empty list for re-planning access
        if payload.context:
            chat_history = [
                {"role": m.role, "content": m.content, "content_type": m.content_type}
                for m in payload.context
            ]
        
        # Run Phase 2 pipeline
        phase2_result = await run_phase2_pipeline(
            atoms=atoms,
            workspace_id=workspace_id,
            chat_history=chat_history,
            thread_id=payload.thread_id,
            session_id=payload.session_id,
        )
        
        # Extract results from Phase 2
        enriched_atoms = phase2_result.enriched_atoms
        phase2_results = phase2_result.evaluation_results

        # ─────────────────── EARLY EXIT: CLARIFICATION ──────────────────
        # Clarification takes precedence - if ANY atom needs clarification,
        # short-circuit immediately and don't proceed to Phase 3/4
        if phase2_result.clarification_atoms:
            logger.info(
                f"[Pipeline] ⚡ EARLY EXIT: {len(phase2_result.clarification_atoms)} clarification atom(s) detected - "
                f"skipping Phase 3/4"
            )
            
            # Process first clarification atom (prioritize single clarification)
            # TODO: Support multiple clarifications in one request
            clarification_atom = phase2_result.clarification_atoms[0]
            clarification_prompt = clarification_atom.tool_arguments.get("prompt", clarification_atom.description)
            
            # Detect domain and missing fields using ClarificationManager helpers
            domain = detect_domain_from_atom(clarification_atom)
            missing_fields = detect_missing_fields_for_domain(domain, clarification_atom.tool_arguments)
            
            # Add clarification to persistent session
            clarification_entry = await session_service.add_clarification(
                session=session,
                atom_id=str(clarification_atom.id),
                domain=domain,
                missing_fields=missing_fields,
                prompt=clarification_prompt,
            )
            clarification_id = clarification_entry.clarification_id
            
            # Prepare sanitized phase2 result for storage (remove heavy redundant lists)
            phase2_dump = phase2_result.__dict__ if hasattr(phase2_result, '__dict__') else {}
            phase2_sanitized = {k: v for k, v in phase2_dump.items() if k not in ('enriched_atoms', 'evaluation_results', 'clarification_atoms')}
            
            # Save checkpoint at Phase 2 for resume
            await session_service.save_checkpoint(
                session=session,
                phase=2,
                current_node="clarification_wait",
                phase_result=phase2_sanitized,
                atoms_pending=[a.model_dump() for a in enriched_atoms] if enriched_atoms else [],
            )
            
            logger.info(
                f"[Pipeline] Created persistent clarification {clarification_id} "
                f"for session {session.session_id}, domain={domain}, missing={missing_fields}"
            )
            
            # Also create in-memory session for backward compatibility
            # (legacy handlers may still check in-memory store)
            try:
                ClarificationManager.create_session(
                    session_id=payload.session_id,
                    original_message=payload.message,
                    missing_fields=missing_fields,
                    domain=domain,
                    initial_prompt=clarification_prompt,
                    clarification_id=clarification_id,  # Use the same ID
                )
            except Exception as legacy_err:
                logger.debug(f"[Pipeline] Legacy clarification session creation (optional): {legacy_err}")
            
            # Build early exit response
            clarification_response = {
                "status": "success",
                "message": f"🤔 {clarification_prompt}",
                "ui_state": "clarification_needed",
                "session_id": session.session_id,  # Include session_id for frontend
                "formatted_response": {
                    "message": f"🤔 {clarification_prompt}",
                    "ui_state": "clarification_needed",
                    "status": "CLARIFICATION_REQUIRED",
                    "clarification_id": clarification_id,
                    "session_id": session.session_id,
                    "data": {
                        "input_type": clarification_atom.tool_arguments.get("input_type", "clarification"),
                        "prompt": clarification_prompt,
                        "capability": clarification_atom.tool_arguments.get("capability"),
                        "missing_fields": missing_fields,
                        "domain": domain,
                    },
                },
                "phases": {
                    "phase1": {
                        "classification": {
                            "intent": classification.intent,
                            "complexity_score": classification.complexity_score,
                            "domain": classification.domain,
                            "risk_level": classification.risk_level,
                        },
                        "atoms_count": len(atoms),
                    },
                    "phase2": {
                        "enriched_count": len(enriched_atoms),
                        "clarification_count": phase2_result.clarification_count,
                        "early_exit": True,
                        "early_exit_reason": "clarification_required",
                    },
                    "phase3": {"skipped": True, "reason": "Early exit for clarification"},
                    "phase4": {"skipped": True, "reason": "Early exit for clarification"},
                },
                "summary": {
                    "total_atoms": len(atoms),
                    "clarification_atoms": len(phase2_result.clarification_atoms),
                    "early_exit": True,
                    "session_id": session.session_id,
                },
            }
            
            return JSONResponse(content=jsonable_encoder(clarification_response))


        # ─────────────────── PHASE 3: CONTROL (via router function) ──────────────────

        # Run Phase 3 pipeline
        phase3_result = await run_phase3_pipeline(
            atoms=enriched_atoms,
            org_id=payload.org_id or "default_org",
            workspace_id=workspace_id,
        )
        
        # Extract results from Phase 3
        approved_atoms = phase3_result.approved_atoms
        phase3_results = phase3_result.validation_results

        # ─────────────────── EARLY EXIT: APPROVAL REQUIRED ──────────────────
        # If ANY atom requires approval, ALL atoms wait - don't proceed to Phase 4
        # Filter out clarification atoms (they shouldn't trigger approval wait)
        approval_required_atoms = [
            result for result in phase3_results
            if result.get("requires_approval")
            and not (
                result.get("atom")
                and result.get("atom").target_agent == "HUMAN_USER"
                and result.get("atom").tool_name in ("user_input_required", "human_input")
            )
        ]
        
        if approval_required_atoms:
            logger.info(
                f"[Pipeline] ⚡ EARLY EXIT: {len(approval_required_atoms)} atom(s) require approval - "
                f"ALL atoms waiting, skipping Phase 4"
            )
            
            # Build approval items for response and track in session
            approval_items = []
            for result in approval_required_atoms:
                atom = result.get("atom")
                approval_id = result.get("approval_id")
                
                if atom and approval_id:
                    # Track approval in session state
                    await session_service.track_approval(
                        session=session,
                        approval_id=approval_id,
                        atom_id=str(atom.id),
                        atom_snapshot=atom.model_dump(),
                    )
                    
                    # Determine skill category for display
                    skill_category = "general"
                    tool_name = atom.tool_name or ""
                    if "email" in tool_name.lower():
                        skill_category = "communication"
                    elif "calendar" in tool_name.lower() or "event" in tool_name.lower():
                        skill_category = "calendar"
                    elif "task" in tool_name.lower() or "todo" in tool_name.lower():
                        skill_category = "task"
                    
                    approval_items.append({
                        "approval_id": approval_id,
                        "atom_id": str(atom.id),
                        "skill": tool_name or "unknown",
                        "skill_category": skill_category,
                        "type": "policy_approval",
                        "action_description": atom.description,
                        "reason": result.get("approval_reason", "Policy requires approval"),
                        "risk_level": atom.risk_level,
                        "status": "PENDING",
                        "display": atom.tool_arguments or {},
                    })
            
            # Prepare sanitized phase3 result for storage
            phase3_dump = phase3_result.__dict__ if hasattr(phase3_result, '__dict__') else {}
            phase3_sanitized = {k: v for k, v in phase3_dump.items() if k not in ('approved_atoms', 'blocked_atoms', 'pending_approval_atoms', 'validation_results')}
            
            # Save checkpoint at Phase 3 for resume after approval
            await session_service.save_checkpoint(
                session=session,
                phase=3,
                current_node="approval_wait",
                phase_result=phase3_sanitized,
                atoms_pending=[a.model_dump() for a in approved_atoms] if approved_atoms else [],
            )
            
            logger.info(
                f"[Pipeline] Tracked {len(approval_items)} approvals in session {session.session_id}"
            )
            
            # Build approval message
            action_descriptions = [item["action_description"] for item in approval_items]
            approval_count = len(approval_items)
            if approval_count == 1:
                approval_message = f"⚠️ Action requires approval: {action_descriptions[0]}"
            else:
                actions_text = ", ".join(action_descriptions[:3])
                if approval_count > 3:
                    actions_text += f" and {approval_count - 3} more"
                approval_message = f"⚠️ {approval_count} action(s) require your approval: {actions_text}"
            
            # Build early exit response
            approval_response = {
                "status": "success",
                "message": approval_message,
                "ui_state": "approval_needed",
                "session_id": session.session_id,  # Include session_id for frontend
                "formatted_response": {
                    "message": approval_message,
                    "ui_state": "approval_needed",
                    "status": "APPROVAL_REQUIRED",
                    "session_id": session.session_id,
                    "output": {
                        "approval_count": approval_count,
                        "actions": action_descriptions,
                        "all_atoms_waiting": True,
                    },
                },
                "approval_request": {
                    "total_approvals": approval_count,
                    "items": approval_items,
                    "all_atoms_waiting": True,
                    "session_id": session.session_id,
                },
                "phases": {
                    "phase1": {
                        "classification": {
                            "intent": classification.intent,
                            "complexity_score": classification.complexity_score,
                            "domain": classification.domain,
                            "risk_level": classification.risk_level,
                        },
                        "atoms_count": len(atoms),
                    },
                    "phase2": {
                        "enriched_count": len(enriched_atoms),
                        "ready_count": phase2_result.ready_count,
                        "requires_approval_count": phase2_result.requires_approval_count,
                        "blocked_count": phase2_result.blocked_count,
                    },
                    "phase3": {
                        "approved_count": len(approved_atoms),
                        "blocked_count": phase3_result.blocked_count,
                        "approval_required_count": len(approval_required_atoms),
                        "early_exit": True,
                        "early_exit_reason": "approval_required",
                    },
                    "phase4": {"skipped": True, "reason": "Early exit for approval"},
                },
                "summary": {
                    "total_atoms": len(atoms),
                    "approval_required_atoms": len(approval_required_atoms),
                    "all_atoms_waiting": True,
                    "early_exit": True,
                    "session_id": session.session_id,
                },
            }
            
            return JSONResponse(content=jsonable_encoder(approval_response))


        # ─────────────────── PHASE 4: EXECUTION (via router function) ──────────────────

        # Import callback factories
        # Import callback factories (clarification handled by early exit, not needed here)
        from app.phase4.callbacks import (
            create_replan_handler,
            create_memory_handler,
        )

        # Create callbacks with proper context
        replan_handler = create_replan_handler(
            workspace_id=workspace_id,
            user_id=payload.user_id,
            chat_history=chat_history,
        )
        memory_handler = create_memory_handler(
            org_id=payload.org_id or "default_org",
            workspace_id=workspace_id,
            user_id=payload.user_id,
            original_message=payload.message,
            background_tasks=None,
        )

        # Run Phase 4 pipeline with callbacks
        # NOTE: clarification_handler removed - clarification atoms now exit early after Phase 2
        phase4_result = await run_phase4_pipeline(
            atoms=approved_atoms,
            org_id=payload.org_id or "default_org",
            workspace_id=workspace_id,
            original_message=payload.message,
            replan_callback=replan_handler,
            memory_callback=memory_handler,
        )

        # Extract results from Phase 4
        phase4_results = phase4_result.execution_results
        executed_atoms = phase4_result.executed_atoms
        dispatcher_results = phase4_result.dispatcher_results
        atom_outputs = phase4_result.atom_outputs
        formatted_response = phase4_result.formatted_response

        # ─────────────────── PHASE 5: MEMORY INGESTION + EVALUATION (SAFE MODE) ──────────────────
        # Per approved spec: Memory Write Path Finalization Plan
        import os
        from datetime import datetime as dt_module
        
        MEMORY_WRITE_ENABLED = os.getenv("GENIOS_MEMORY_WRITE_ENABLED", "true").lower() == "true"
        
        # Guard conditions for memory ingestion (all must pass)
        should_ingest_memory = (
            MEMORY_WRITE_ENABLED
            and phase4_result.success
            and phase4_result.success_count > 0
            and session.status == SessionStatus.ACTIVE
            and session.clarifications.active_clarification_id is None
            and session.approvals.pending_count == 0
            and not session.checkpoint.memory_ingested
        )
        
        if should_ingest_memory:
            try:
                from app.phase2.memory import ingest_memory_from_workflow
                
                # Build workflow data for memory ingestion (governance: completed workflows only)
                workflow_data = {
                    "status": "completed",
                    "goal": payload.message,
                    "actions": [
                        {
                            "atom_id": str(a.id),
                            "tool": a.tool_name,
                            "description": a.description,
                            "status": a.status,
                        }
                        for a in executed_atoms
                    ],
                    "outcome": "success" if phase4_result.success_count > 0 else "failed",
                    "execution_results": phase4_results,
                    "has_rejections": False,
                    "has_clarifications": False,
                }
                
                # Run memory ingestion synchronously (no background task)
                memory_result = await ingest_memory_from_workflow(
                    org_id=payload.org_id or "default_org",
                    workspace_id=workspace_id,
                    workflow_id=session.session_id,
                    workflow_data=workflow_data,
                    user_id=payload.user_id,
                )
                
                # Mark as ingested (idempotency - exactly once per workflow)
                session.checkpoint.memory_ingested = True
                session.checkpoint.memory_ingested_at = dt_module.utcnow()
                await session_service.save_session(session)
                
                logger.info(f"[Phase5] Memory ingestion complete for session {session.session_id}: {memory_result}")
                
            except Exception as mem_err:
                logger.warning(f"[Phase5] Memory ingestion failed: {mem_err}", exc_info=True)
        else:
            if not MEMORY_WRITE_ENABLED:
                logger.info("⏭️ Memory writes disabled via GENIOS_MEMORY_WRITE_ENABLED")
            elif session.checkpoint.memory_ingested:
                logger.info(f"[Phase5] Memory already ingested for session {session.session_id}")
            else:
                logger.debug("[Phase5] Memory ingestion skipped (guard conditions not met)")
        
        # Phase 5 Analytics (SAFE MODE - learning disabled, run synchronously)
        if MEMORY_WRITE_ENABLED and phase4_result.success and phase4_result.success_count > 0:
            execution_start_time = dt_module.utcnow()
            try:
                eval_result = await phase5.evaluate_session(
                    session_id=session.session_id,
                    request_id=session.session_id,
                    org_id=payload.org_id or "default_org",
                    workspace_id=workspace_id,
                    user_id=payload.user_id or "default_user",
                    user_goal=payload.message,
                    execution_results=phase4_results,
                    start_time=execution_start_time,
                    atoms=executed_atoms,
                )
                logger.info(f"✅ Phase 5 evaluation complete (Safe Mode): {eval_result}")
            except Exception as e:
                logger.error(f"Phase 5 evaluation failed: {e}", exc_info=True)
        else:
            logger.info("⏭️ Phase 5 skipped (disabled or no successful executions)")

        # ─────────────────── ENHANCE CLARIFICATION WITH SUCCESSFUL RESULTS ──────────────────
        # If formatted_response was set for clarification, add any successful execution results
        if formatted_response and formatted_response.get("status") == "CLARIFICATION_REQUIRED":
            successful_results = []
            for dr in dispatcher_results:
                if dr.status == ExecutionStatus.SUCCESS:
                    matching_atom = next(
                        (a for a in executed_atoms if a.id == dr.atom_id), None
                    )
                    successful_results.append({
                        "atom_id": str(dr.atom_id),
                        "description": matching_atom.description if matching_atom else "Unknown",
                        "skill": matching_atom.tool_name if matching_atom else "Unknown",
                        "output": dr.output,
                        "status": "SUCCESS",
                    })
            if successful_results:
                formatted_response["data"]["successful_executions"] = successful_results

        # ─────────────────── FORMAT RESPONSES FOR FRONTEND ──────────────────

        # Format Phase 4 execution results
        # formatted_response may have been set during Phase 4 loop for clarifications
        # If not set, format other response types
        if not formatted_response:
            # Check if approvals are needed (second priority)
            # Filter out HUMAN_USER clarification atoms - they are handled separately above
            def is_real_approval(r):
                if not r.get("requires_approval"):
                    return False
                atom = r.get("atom")
                if (
                    atom
                    and atom.target_agent == "HUMAN_USER"
                    and atom.tool_name in ("user_input_required", "human_input")
                ):
                    return False  # This is a clarification, not an approval
                return True

            real_approval_items = [r for r in phase3_results if is_real_approval(r)]

            if real_approval_items:
                # Approval needed - format approval message with action details
                approval_items = real_approval_items
                approval_count = len(approval_items)

                # Build list of action descriptions
                action_descriptions = []
                for item in approval_items:
                    atom = item.get("atom")
                    if atom:
                        action_descriptions.append(atom.description)

                # Format the message with action details
                if approval_count == 1 and action_descriptions:
                    approval_message = (
                        f"⚠️ Action requires your approval: {action_descriptions[0]}"
                    )
                elif action_descriptions:
                    actions_text = ", ".join(
                        action_descriptions[:3]
                    )  # Show up to 3 actions
                    if approval_count > 3:
                        actions_text += f" and {approval_count - 3} more"
                    approval_message = f"⚠️ {approval_count} action(s) require your approval: {actions_text}"
                else:
                    approval_message = f"⚠️ {approval_count} operation(s) require your approval before execution."

                # Build successful execution results to include in response
                successful_results = []
                for dr in dispatcher_results:
                    if dr.status == ExecutionStatus.SUCCESS:
                        # Find the atom for this result
                        matching_atom = next(
                            (a for a in executed_atoms if a.id == dr.atom_id), None
                        )
                        successful_results.append({
                            "atom_id": str(dr.atom_id),
                            "description": matching_atom.description if matching_atom else "Unknown",
                            "skill": matching_atom.tool_name if matching_atom else "Unknown",
                            "output": dr.output,
                            "status": "SUCCESS",
                        })

                formatted_response = {
                    "message": approval_message,
                    "ui_state": "approval_needed",
                    "status": "APPROVAL_REQUIRED",
                    "output": {
                        "approval_count": approval_count,
                        "actions": action_descriptions,
                        "successful_executions": successful_results,  # Include successful results
                    },
                }

        # Format execution results if we have dispatcher results and no response yet
        if not formatted_response and dispatcher_results:
            # Create atom descriptions map for better formatting
            atom_descriptions = {
                str(atom.id): atom.description for atom in executed_atoms
            }

            # Format multiple results
            if len(dispatcher_results) == 1:
                # Single result - format individually
                formatted_response = ResponseFormatter.format_dispatcher_result(
                    dispatcher_results[0],
                    atom_descriptions.get(str(dispatcher_results[0].atom_id)),
                )
            else:
                # Multiple results - format combined
                formatted_response = ResponseFormatter.format_multiple_results(
                    dispatcher_results, atom_descriptions
                )

        # ─────────────────── BUILD EXECUTION PLAN FOR FRONTEND ──────────────────

        # Build execution_plan: grouped by skill/agent with results
        execution_plan = []

        # Include both executed_atoms and paused_atoms (awaiting approval)
        all_atoms_for_plan = executed_atoms + [
            a for a in approved_atoms if a.status == "PAUSED"
        ]

        for atom in all_atoms_for_plan:
            # Find corresponding dispatcher result (if executed)
            dispatcher_result_dict = None
            for dr in dispatcher_results:
                if dr.atom_id == atom.id:
                    dispatcher_result_dict = dr.to_dict()
                    break

            # Determine skill category for UI hints
            skill_category = "general"
            if atom.tool_name and "email" in atom.tool_name.lower():
                skill_category = "communication"
            elif atom.tool_name and (
                "calendar" in atom.tool_name.lower()
                or "event" in atom.tool_name.lower()
            ):
                skill_category = "calendar"
            elif atom.tool_name and (
                "task" in atom.tool_name.lower() or "todo" in atom.tool_name.lower()
            ):
                skill_category = "task"

            # Extract full output details for rich display
            output_data = None
            action_link = None
            display_data = {}  # Skill-specific display fields

            # If executed, use dispatcher output
            if (
                dispatcher_result_dict
                and dispatcher_result_dict.get("status") == "SUCCESS"
            ):
                output = dispatcher_result_dict.get("output", {})
                if isinstance(output, dict):
                    output_data = output  # Keep full output for display

                    # Extract skill-specific display data
                    if skill_category == "calendar":
                        display_data = {
                            "title": output.get("title", "Event"),
                            "start_time": output.get("start_time"),
                            "end_time": output.get("end_time"),
                            "location": output.get("location"),
                            "attendees": output.get("attendees", []),
                            "description": output.get("description"),
                            "resource_link": output.get("resource_link"),
                        }
                        if "resource_link" in output:
                            action_link = {
                                "type": "open",
                                "text": "View Event",
                                "url": output.get("resource_link"),
                                "target": "_blank",
                            }

                    elif skill_category == "communication":
                        display_data = {
                            "to": output.get("to", []),
                            "subject": output.get("subject", ""),
                            "body": output.get("body", ""),
                            "cc": output.get("cc", []),
                            "bcc": output.get("bcc", []),
                            "draft_id": output.get("draft_id"),
                            "message_id": output.get("message_id"),
                            "status": output.get("status"),
                            "resource_link": output.get("resource_link"),
                        }
                        if output.get("draft_id") or output.get("resource_link"):
                            action_link = {
                                "type": "open",
                                "text": (
                                    "Open Draft" if output.get("draft_id") else "View"
                                ),
                                "url": output.get("resource_link"),
                                "target": "_blank",
                            }

                    elif skill_category == "task":
                        display_data = {
                            "title": output.get("title", "Task"),
                            "description": output.get("description"),
                            "due_date": output.get("due_date"),
                            "priority": output.get("priority"),
                            "assignee": output.get("assignee"),
                            "status": output.get("status"),
                            "resource_link": output.get("resource_link"),
                        }
                        if "resource_link" in output:
                            action_link = {
                                "type": "open",
                                "text": "View Task",
                                "url": output.get("resource_link"),
                                "target": "_blank",
                            }

                    else:
                        # Generic display data
                        display_data = output

            # If PAUSED (awaiting approval), use tool_arguments for display
            elif atom.status == "PAUSED":
                tool_args = atom.tool_arguments or {}
                context_data = atom.context_data or {}

                if skill_category == "calendar":
                    display_data = {
                        "title": tool_args.get("title") or tool_args.get("event_title"),
                        "start_time": tool_args.get("start_time"),
                        "end_time": tool_args.get("end_time"),
                        "location": tool_args.get("location"),
                        "attendees": tool_args.get("attendees", []),
                        "description": tool_args.get("description"),
                    }

                elif skill_category == "communication":
                    to_value = tool_args.get("to", [])
                    if not isinstance(to_value, list):
                        to_value = [to_value] if to_value else []

                    if tool_args.get("body"):
                        tool_args["body"] = tool_args.get("body", "")
                    else:
                        tool_args["body"] = ""
                    display_data = {
                        "to": to_value,
                        "subject": tool_args.get("subject", ""),
                        "body": tool_args["body"],  # Preview
                        "cc": tool_args.get("cc", []),
                        "bcc": tool_args.get("bcc", []),
                        "draft_id": context_data.get("draft_id"),
                        "resource_link": context_data.get("draft_link"),
                    }
                    if context_data.get("draft_link"):
                        action_link = {
                            "type": "open",
                            "text": "Preview Draft",
                            "url": context_data.get("draft_link"),
                            "target": "_blank",
                        }

                elif skill_category == "task":
                    display_data = {
                        "title": tool_args.get("title"),
                        "description": tool_args.get("description"),
                        "due_date": tool_args.get("due_date"),
                        "priority": tool_args.get("priority"),
                        "assignee": tool_args.get("assignee"),
                    }

            plan_item = {
                "atom_id": str(atom.id),
                "skill": atom.tool_name or "unknown_skill",
                "skill_category": skill_category,
                "agent": atom.target_agent or "system",
                "description": atom.description,
                "status": (
                    dispatcher_result_dict.get("status", "PENDING")
                    if dispatcher_result_dict
                    else atom.status
                ),
                "message": None,
                "action_link": action_link,
                "output": output_data,  # Full raw output
                "display": display_data,  # Skill-specific formatted display fields
            }

            # Add message based on status
            if dispatcher_result_dict:
                if dispatcher_result_dict.get("status") == "SUCCESS":
                    plan_item["message"] = f"✅ {atom.description}"
                elif dispatcher_result_dict.get("status") == "FAILED":
                    plan_item["message"] = (
                        f"❌ {atom.description}: {dispatcher_result_dict.get('error', 'Failed')}"
                    )
                elif dispatcher_result_dict.get("status") == "CLARIFICATION_REQUIRED":
                    plan_item["message"] = f"🤔 {atom.description}"
                else:
                    plan_item["message"] = f"⏱️ {atom.description}"
            elif atom.status == "PAUSED":
                # Distinguish between clarification and approval
                if (
                    atom.target_agent == "HUMAN_USER"
                    and atom.tool_name == "user_input_required"
                ):
                    plan_item["message"] = f"🤔 {atom.description}"
                else:
                    plan_item["message"] = f"⏳ {atom.description} (Awaiting approval)"

            execution_plan.append(plan_item)

        # ─────────────────── BUILD APPROVAL REQUEST FOR FRONTEND ──────────────────

        approval_request = None
        # Filter out HUMAN_USER clarification atoms - they are handled via clarification flow, not approval
        approval_items = [
            r
            for r in phase3_results
            if r.get("requires_approval")
            and not (
                r.get("atom")
                and r.get("atom").target_agent == "HUMAN_USER"
                and r.get("atom").tool_name == "user_input_required"
            )
        ]
        if approval_items:
            approval_items_formatted = []
            for item in approval_items:
                atom = item.get("atom")

                # Determine skill category for display
                skill_category = "general"
                tool_name = atom.tool_name if atom else ""
                if tool_name and "email" in tool_name.lower():
                    skill_category = "communication"
                elif tool_name and (
                    "calendar" in tool_name.lower() or "event" in tool_name.lower()
                ):
                    skill_category = "calendar"
                elif tool_name and (
                    "task" in tool_name.lower() or "todo" in tool_name.lower()
                ):
                    skill_category = "task"

                # Extract display data from atom's tool_arguments or context_data
                display_data = {}
                action_link = None

                if atom:
                    tool_args = atom.tool_arguments or {}
                    context_data = atom.context_data or {}

                    # Build display data based on skill category
                    if skill_category == "calendar":
                        display_data = {
                            "title": tool_args.get("title")
                            or tool_args.get("event_title"),
                            "start_time": tool_args.get("start_time"),
                            "end_time": tool_args.get("end_time"),
                            "location": tool_args.get("location"),
                            "attendees": tool_args.get("attendees", []),
                            "description": tool_args.get("description"),
                        }

                    elif skill_category == "communication":
                        display_data = {
                            "to": (
                                tool_args.get("to", [])
                                if isinstance(tool_args.get("to"), list)
                                else (
                                    [tool_args.get("to")] if tool_args.get("to") else []
                                )
                            ),
                            "subject": tool_args.get("subject", ""),
                            "body": tool_args.get("body", ""),
                            "cc": tool_args.get("cc", []),
                            "bcc": tool_args.get("bcc", []),
                            "draft_id": context_data.get("draft_id"),
                            "resource_link": context_data.get("draft_link"),
                        }
                        # Add action link for draft preview
                        if context_data.get("draft_link"):
                            action_link = {
                                "type": "open",
                                "text": "Preview Draft",
                                "url": context_data.get("draft_link"),
                                "target": "_blank",
                            }

                    elif skill_category == "task":
                        display_data = {
                            "title": tool_args.get("title"),
                            "description": tool_args.get("description"),
                            "due_date": tool_args.get("due_date"),
                            "priority": tool_args.get("priority"),
                            "assignee": tool_args.get("assignee"),
                        }

                approval_item = {
                    "approval_id": item.get("approval_id"),
                    "atom_id": str(atom.id) if atom else None,
                    "skill": tool_name or "unknown",
                    "skill_category": skill_category,
                    "type": "policy_approval",
                    "action_description": (atom.description if atom else "Action"),
                    "reason": item.get("approval_reason", "Policy requires approval"),
                    "risk_level": (atom.risk_level if atom else "medium"),
                    "status": "PENDING",
                    "display": display_data,
                    "action_link": action_link,
                }
                approval_items_formatted.append(approval_item)

            approval_request = {
                "total_approvals": len(approval_items_formatted),
                "items": approval_items_formatted,
            }

        # ─────────────────── BUILD EXECUTION SUMMARY FOR FRONTEND ──────────────────

        successful_executions = []
        failed_executions = []

        for plan_item in execution_plan:
            if plan_item["status"] == "SUCCESS":
                successful_executions.append(
                    {
                        "skill": plan_item["skill"],
                        "skill_category": plan_item["skill_category"],
                        "description": plan_item["description"],
                        "message": plan_item["message"],
                        "action_link": plan_item["action_link"],
                        "output": plan_item.get("output"),
                        "display": plan_item.get(
                            "display", {}
                        ),  # Skill-specific fields
                    }
                )
            elif plan_item["status"] == "FAILED":
                failed_executions.append(
                    {
                        "skill": plan_item["skill"],
                        "skill_category": plan_item["skill_category"],
                        "description": plan_item["description"],
                        "message": plan_item["message"],
                        "output": plan_item.get("output"),
                    }
                )

        execution_summary = {
            "total_executed": len(execution_plan),
            "successful": len(successful_executions),
            "failed": len(failed_executions),
            "successful_items": successful_executions,
            "failed_items": failed_executions,
        }

        # ─────────────────── RETURN COMPLETE RESULT ──────────────────

        result = {
            "status": "success",
            "message": (
                formatted_response.get("message") if formatted_response else None
            ),
            "ui_state": (
                formatted_response.get("ui_state") if formatted_response else None
            ),
            # ✨ NEW FRONTEND-FRIENDLY FIELDS
            "execution_plan": execution_plan,
            "execution_summary": execution_summary,
            "approval_request": approval_request,
            # Keep existing debug fields for backward compatibility
            "phases": {
                "phase1": {
                    "classification": {
                        "intent": classification.intent,
                        "complexity_score": classification.complexity_score,
                        "domain": classification.domain,
                        "risk_level": classification.risk_level,
                        "reasoning": classification.reasoning,
                    },
                    "plan_exists": plan is not None,
                    "atoms_count": len(atoms),
                    "debug": phase1_debug,
                },
                "phase2": {
                    "enriched_count": len(enriched_atoms),
                    "results_count": len(phase2_results),
                    "ready_count": phase2_result.ready_count,
                    "requires_approval_count": phase2_result.requires_approval_count,
                    "blocked_count": phase2_result.blocked_count,
                },
                "phase3": {
                    "approved_count": len(approved_atoms),
                    "blocked_count": phase3_result.blocked_count,
                    "approval_required_count": phase3_result.approval_required_count,
                    "guard_failures": {
                        "privacy": phase3_result.privacy_failures,
                        "security": phase3_result.security_failures,
                        "reliability": phase3_result.reliability_failures,
                    },
                    "results_count": len(phase3_results),
                    "approval_details": [
                        {
                            "approval_id": result.get("approval_id"),
                            "atom_id": (
                                str(result.get("atom", {}).id)
                                if result.get("atom")
                                else None
                            ),
                            "requires_approval": result.get("requires_approval", False),
                            "approval_reason": result.get("approval_reason"),
                            "risk_level": (
                                result.get("atom", {}).risk_level
                                if result.get("atom")
                                else None
                            ),
                            "description": (
                                result.get("atom", {}).description
                                if result.get("atom")
                                else None
                            ),
                            "status": (
                                "PENDING"
                                if result.get("requires_approval")
                                else "APPROVED"
                            ),
                        }
                        for result in phase3_results
                        if result.get("requires_approval")
                        # Exclude HUMAN_USER clarification atoms from approval_details
                        and not (
                            result.get("atom")
                            and result.get("atom").target_agent == "HUMAN_USER"
                            and result.get("atom").tool_name == "user_input_required"
                        )
                    ],
                },
                "phase4": {
                    "executed_count": len(executed_atoms),
                    "success_count": phase4_result.success_count,
                    "failed_count": phase4_result.failed_count,
                    "clarification_count": phase4_result.clarification_count,
                    "replan_count": phase4_result.replan_count,
                    "skipped_count": phase4_result.skipped_count,
                    "total_duration_ms": round(phase4_result.total_duration_ms, 2),
                    "results_count": len(phase4_results),
                    "results": phase4_results,
                },
                "phase5": {
                    "status": "queued",
                    "message": "Analytics and learning running in background",
                },
            },
            "summary": {
                "total_atoms": len(atoms),
                "blocked_atoms": sum(
                    1
                    for a in enriched_atoms
                    if hasattr(a, "status") and a.status == "BLOCKED"
                ),
                "approved_atoms": len(approved_atoms),
                "executed_atoms": len(executed_atoms),
            },
            "formatted_response": formatted_response,
        }
        
        # ─────────────────── SAVE THREAD HISTORY ──────────────────
        # Save user message and assistant response with rich details
        try:
            # Build assistant response text with full output details
            assistant_parts = []
            if formatted_response:
                # Main message
                msg = formatted_response.get("message", "")
                if msg:
                    assistant_parts.append(msg)
                
                # Include actual output data (email content, event details, etc.)
                output = formatted_response.get("output")
                if output and isinstance(output, dict):
                    # Extract useful details from output
                    for key in ["response", "data", "results", "events", "emails", "draft"]:
                        val = output.get(key)
                        if val:
                            if isinstance(val, (list, dict)):
                                import json as _json
                                assistant_parts.append(f"{key}: {_json.dumps(val, default=str)[:2000]}")
                            else:
                                assistant_parts.append(f"{key}: {str(val)[:2000]}")
                elif output and isinstance(output, str):
                    assistant_parts.append(output[:2000])
                
                # Approval info
                if formatted_response.get("status") == "APPROVAL_REQUIRED":
                    approval = formatted_response.get("approval_request", {})
                    if approval:
                        assistant_parts.append(f"Approval required: {approval.get('display', {}).get('title', 'Action pending')}")
                
                if not assistant_parts:
                    if formatted_response.get("status") == "SUCCESS":
                        assistant_parts.append(f"Completed {phase4_result.success_count} action(s) successfully.")
            
            assistant_text = "\n".join(assistant_parts) if assistant_parts else ""
            
            if assistant_text:
                # Build rich metadata
                save_metadata = {
                    "intent": classification.intent if classification else None,
                    "domain": classification.domain if classification else None,
                    "risk_level": classification.risk_level if classification else None,
                    "success_count": phase4_result.success_count if phase4_result else 0,
                    "approval_required": formatted_response.get("status") == "APPROVAL_REQUIRED" if formatted_response else False,
                    "atoms_executed": len(executed_atoms) if executed_atoms else 0,
                }
                thread_history_service = get_thread_history_service()
                await thread_history_service.save_exchange(
                    thread_id=payload.thread_id,
                    org_id=payload.org_id or "default_org",
                    workspace_id=workspace_id,
                    user_id=payload.user_id,
                    user_message=payload.message,
                    assistant_response=assistant_text[:4000],
                    session_id=session.session_id,
                    metadata=save_metadata,
                )
        except Exception as th_err:
            logger.warning(f"[ThreadHistory] Failed to save exchange: {th_err}")

        return JSONResponse(content=jsonable_encoder(result))

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ Pipeline error: {error_msg}", exc_info=True)
        return JSONResponse(status_code=500, content={"detail": error_msg})



