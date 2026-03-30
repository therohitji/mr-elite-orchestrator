# app/routers/phase1.py
"""
Phase 1 Router: Planning Phase API Endpoints

Provides:
1. Reusable `run_phase1_pipeline()` function for main.py integration
2. Isolated endpoints to test Phase 1 components

Core Function:
- run_phase1_pipeline: Executes full Phase 1 (pre-flight → classify → plan → atomize)

Test Endpoints:
- POST /phase1/test - Full Phase 1 with debug output
- POST /phase1/classify - Classification only
- POST /phase1/plan - Planning only
"""

import logging
from typing import Optional, List, Dict, Any
from uuid import uuid4, UUID
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

# Import shared utilities
from app.routers.shared import serialize_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/phase1", tags=["Phase 1 - Planning"])


# ─────────────────── PHASE 1 RESULT DATACLASS ──────────────────

@dataclass
class Phase1Result:
    """Result from running Phase 1 pipeline"""
    # Status
    success: bool
    phase_reached: str  # "pre_flight", "heuristic", "semantic", "planning", "atomization"
    
    # Early exit response (if success=False or non-goal/task intent)
    early_exit: bool = False
    early_exit_response: Optional[Dict[str, Any]] = None
    
    # Classification results
    heuristic_result: Optional[str] = None
    classification: Optional[Any] = None  # SemanticClassification
    
    # Planning results
    plan: Optional[Any] = None  # ExecutionPlan
    
    # Atomization results
    atoms: Optional[List[Any]] = None  # List[ExecutionAtom]
    atomization_debug: Optional[Dict[str, Any]] = None
    
    # Error info
    error: Optional[str] = None
    error_type: Optional[str] = None


# ─────────────────── CORE PIPELINE FUNCTION ──────────────────

async def run_phase1_pipeline(
    message: str,
    workspace_id: str,
    user_id: Optional[str] = None,
    org_id: Optional[str] = None,
    context: Optional[List[Dict[str, Any]]] = None,
    generate_chat_response: bool = True,
) -> Phase1Result:
    """
    Execute the complete Phase 1 pipeline.
    
    This is the core function that main.py calls. It returns a Phase1Result
    containing atoms ready for Phase 2, or an early exit response for
    chat/query intents.
    
    Args:
        message: User's input message
        workspace_id: Workspace ID for Supabase lookups
        user_id: Optional user ID for context
        org_id: Optional org ID for knowledge retrieval (memories)
        context: Optional chat history as list of {role, content, content_type}
        generate_chat_response: If True, generate LLM response for chat intent
        
    Returns:
        Phase1Result with atoms, classification, or early_exit_response
    """
    from app.phase1.pre_flight_check import check_blocking_policies
    from app.phase1.classifier import heuristic_classify
    from app.phase1.semantic_router import semantic_classify
    from app.phase1.cot_planner import run_cot_planner
    from app.phase1.atomizer import AtomizerEngine
    from app.core.gemini_client import call_gemini_text
    
    try:
        # ─────────── STEP 1: PRE-FLIGHT CHECK ───────────
        try:
            pre_flight = await check_blocking_policies(message, supabase_client=None)
        except Exception as e:
            logger.error(f"Pre-flight check error: {e}")
            pre_flight = {"blocked": False}
        
        if pre_flight.get("blocked"):
            logger.warning(f"🚫 Pre-flight BLOCKED: {pre_flight.get('reason')}")
            return Phase1Result(
                success=False,
                phase_reached="pre_flight",
                early_exit=True,
                early_exit_response={
                    "status": "blocked",
                    "phase": "pre_flight",
                    "reason": pre_flight.get("reason"),
                },
                error=f"Blocked: {pre_flight.get('reason')}",
            )
        
        # ─────────── STEP 2: HEURISTIC CLASSIFICATION ───────────
        heuristic_result = heuristic_classify(message)
        
        if heuristic_result == "chat":
            # Generate chat response if requested
            chat_response = "Hello! How can I help you today?"
            if generate_chat_response:
                try:
                    chat_prompt = f"""
                    User said: {message}
                    
                    This is a casual conversation. Please respond in a friendly, helpful manner.
                    Be concise and natural. If they're greeting you, greet them back warmly.
                    """
                    chat_response = await call_gemini_text(chat_prompt)
                except Exception as e:
                    logger.error(f"Failed to generate chat response: {e}")
            
            return Phase1Result(
                success=True,
                phase_reached="heuristic",
                early_exit=True,
                heuristic_result="chat",
                early_exit_response={
                    "phase": "heuristic",
                    "execution_end": True,
                    "ui_state": "query_response",
                    "message": chat_response,
                    "formatted_response": {
                        "ui_state": "query_response",
                        "status": "SUCCESS",
                        "output": {"response": chat_response},
                    },
                },
            )
        elif heuristic_result in ["interrupt", "system", "error"]:
            return Phase1Result(
                success=True,
                phase_reached="heuristic",
                early_exit=True,
                heuristic_result=heuristic_result,
                early_exit_response={
                    "phase": "heuristic",
                    "execution_end": True,
                    "output": {"result": heuristic_result},
                },
            )
        
        # ─────────── STEP 3: SEMANTIC CLASSIFICATION ───────────
        try:
            classification = semantic_classify(message)
        except Exception as e:
            logger.error(f"Classification error: {e}")
            return Phase1Result(
                success=False,
                phase_reached="semantic",
                heuristic_result=heuristic_result,
                error=f"Classification failed: {str(e)}",
                error_type=type(e).__name__,
            )
        
        # Handle QUERY intent (needs LLM response, no execution)
        if classification.intent == "query":
            query_response = "I'm not sure how to answer that."
            if generate_chat_response:
                try:
                    # ─────────── RAG: Retrieve relevant knowledge ───────────
                    knowledge_context_block = ""
                    knowledge_sources_used = []
                    try:
                        from app.core.knowledge_retriever import KnowledgeRetriever
                        retriever = KnowledgeRetriever()
                        knowledge = await retriever.retrieve_for_query(
                            query=message,
                            org_id=org_id or "default_org",
                            workspace_id=workspace_id,
                        )
                        if knowledge.has_context:
                            knowledge_context_block = retriever.format_as_prompt_context(knowledge)
                            if knowledge.policies:
                                knowledge_sources_used.append("policies")
                            if knowledge.resource_chunks:
                                knowledge_sources_used.append("resources")
                            if knowledge.memories:
                                knowledge_sources_used.append("memories")
                            logger.info(
                                f"[Phase1] RAG context injected for query: "
                                f"sources={knowledge_sources_used}"
                            )
                    except Exception as rag_err:
                        logger.warning(f"[Phase1] Knowledge retrieval failed (non-fatal): {rag_err}")

                    # Build context string
                    context_str = ""
                    if context:
                        context_str = "\n\nRecent Conversation History:\n"
                        for msg in context[-10:]:
                            context_str += f"- {msg.get('role', 'user').upper()}: {msg.get('content', '')[:500]}\n"
                    
                    query_prompt = f"""
                    User asked: {message}
                    
                    {knowledge_context_block}

                    Classification:
                    - Intent: {classification.intent}
                    - Domain: {classification.domain}
                    - Reasoning: {classification.reasoning}
                    {context_str}
                    
                    INSTRUCTIONS:
                    - If "Knowledge Context" is provided above, use it as your PRIMARY source of truth.
                    - For questions about company policies, rules, or procedures, cite the specific
                      policy from COMPANY POLICIES section. Be precise and factual.
                    - For questions that match knowledge base content, reference the relevant
                      resource content. Mention the source document when available.
                    - For questions about past interactions or preferences, use RELEVANT MEMORIES.
                    - If the user is asking about previous conversations, past interactions, or chat history,
                      use the "Recent Conversation History" above. Summarize, recall,
                      or answer based on what was discussed.
                    - If conversation history is available, reference specific details (actions taken, emails sent,
                      meetings scheduled, topics discussed) to give a thorough answer.
                    - If no knowledge context or conversation history contains relevant info, say so politely
                      and provide your best helpful response.
                    - Do NOT mention that you are searching a "knowledge base" or "database" — just
                      answer naturally as if you know this information.
                    """
                    query_response = await call_gemini_text(query_prompt)
                except Exception as e:
                    logger.error(f"Failed to generate query response: {e}")
            
            return Phase1Result(
                success=True,
                phase_reached="semantic",
                early_exit=True,
                heuristic_result=heuristic_result,
                classification=classification,
                early_exit_response={
                    "phase": "phase1",
                    "execution_end": True,
                    "ui_state": "query_response",
                    "message": query_response,
                    "classification": {
                        "intent": classification.intent,
                        "complexity_score": classification.complexity_score,
                        "domain": classification.domain,
                        "risk_level": classification.risk_level,
                        "reasoning": classification.reasoning,
                    },
                    "formatted_response": {
                        "ui_state": "query_response",
                        "status": "SUCCESS",
                        "output": {"response": query_response},
                    },
                    "knowledge_sources": knowledge_sources_used if generate_chat_response else [],
                },
            )
        
        # ─────────── STEP 4: COT PLANNING (for goal/task) ───────────
        if classification.intent not in ["goal", "task"]:
            # Unexpected intent, return early
            return Phase1Result(
                success=True,
                phase_reached="semantic",
                early_exit=True,
                classification=classification,
                early_exit_response={
                    "phase": "phase1",
                    "execution_end": True,
                    "message": f"Classified as {classification.intent} - no execution needed",
                },
            )
        
        try:
            logger.info(f"Starting CoT planning for: {message[:100]}...")
            
            # Build context for CoT planner - separate thread_history from chat_history
            chat_context = {}
            if context:
                # Extract thread_history content (formatted string from ThreadHistoryService)
                thread_history_items = [c for c in context if c.get("content_type") == "thread_history"]
                other_items = [c for c in context if c.get("content_type") != "thread_history"]
                
                if thread_history_items:
                    chat_context["thread_history"] = thread_history_items[0].get("content", "")
                if other_items:
                    chat_context["chat_history"] = other_items
            
            plan = await run_cot_planner(
                message,
                workspace_id=workspace_id,
                context=chat_context,
            )
            logger.info(f"✅ Plan generated with {len(plan.plan)} steps")
            
        except Exception as e:
            logger.error(f"Planning error: {type(e).__name__}: {e}", exc_info=True)
            return Phase1Result(
                success=False,
                phase_reached="planning",
                heuristic_result=heuristic_result,
                classification=classification,
                error=f"Planning failed: {str(e)}",
                error_type=type(e).__name__,
            )
        
        # ─────────── STEP 5: ATOMIZATION ───────────
        try:
            logger.info(f"Starting atomization with workspace_id={workspace_id}...")
            atomizer = AtomizerEngine()
            atomization_result = await atomizer.atomize(
                plan,
                workspace_id,
                user_id=user_id,
                user_input=message,
            )
            
            atoms = atomization_result["atoms"]
            logger.info(f"✅ Atomization complete: {len(atoms)} atoms from {len(plan.plan)} steps")
            
            # Build debug info
            atomization_debug = {
                "total_steps": atomization_result["total_steps"],
                "atoms_created": atomization_result["atoms_created"],
                "atoms_rejected": atomization_result["atoms_rejected"],
                "matching_summary": atomization_result["matching_summary"],
            }
            
            return Phase1Result(
                success=True,
                phase_reached="atomization",
                early_exit=False,
                heuristic_result=heuristic_result,
                classification=classification,
                plan=plan,
                atoms=atoms,
                atomization_debug=atomization_debug,
            )
            
        except Exception as e:
            logger.error(f"Atomization error: {type(e).__name__}: {e}", exc_info=True)
            return Phase1Result(
                success=False,
                phase_reached="atomization",
                heuristic_result=heuristic_result,
                classification=classification,
                plan=plan,
                error=f"Atomization failed: {str(e)}",
                error_type=type(e).__name__,
            )
            
    except Exception as e:
        logger.error(f"Phase 1 pipeline error: {e}", exc_info=True)
        return Phase1Result(
            success=False,
            phase_reached="unknown",
            error=str(e),
            error_type=type(e).__name__,
        )



# ─────────────────── REQUEST SCHEMAS ──────────────────

class Phase1Request(BaseModel):
    """Request payload for Phase 1 testing"""
    message: str
    workspace_id: str = "1f2511ac-bd16-46e1-91b5-eb8901a6731e"  # Default from your Supabase
    user_id: Optional[str] = None
    org_id: str = "default_org"
    
    # Optional context
    context: Optional[List[Dict[str, str]]] = None


class ClassifyOnlyRequest(BaseModel):
    """Request for classification only (no planning)"""
    message: str


class PlanOnlyRequest(BaseModel):
    """Request for planning only (skip classification)"""
    message: str
    workspace_id: str = "1f2511ac-bd16-46e1-91b5-eb8901a6731e"
    context: Optional[Dict[str, Any]] = None


# ─────────────────── ALIASED HELPERS ──────────────────
# Aliased from shared module for backward compatibility
_serialize_uuid = serialize_uuid


# ─────────────────── ENDPOINTS ──────────────────

@router.post("/test")
async def test_phase1_complete(request: Phase1Request):
    """
    🧪 TEST PHASE 1 COMPLETE
    
    Runs the full Phase 1 pipeline in isolation using run_phase1_pipeline().
    Returns all intermediate results for debugging.
    
    **Use this to verify:**
    - Classification is working correctly
    - Plan steps use correct agent/tool names from Supabase
    - Atoms have correct parameters
    """
    try:
        # Run the pipeline
        result = await run_phase1_pipeline(
            message=request.message,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
            org_id=request.org_id,
            context=request.context,
            generate_chat_response=False,  # Skip LLM response for testing
        )
        
        # Build detailed response
        response = {
            "request": {
                "message": request.message,
                "workspace_id": request.workspace_id,
            },
            "status": "success" if result.success else "error",
            "phase_reached": result.phase_reached,
            "early_exit": result.early_exit,
        }
        
        # Add phase-specific results
        phases = {}
        
        # Heuristic
        if result.heuristic_result:
            phases["heuristic"] = {
                "result": result.heuristic_result,
                "skipped_semantic": result.heuristic_result in ["chat", "interrupt", "system", "error"],
            }
        
        # Classification
        if result.classification:
            phases["semantic"] = {
                "intent": result.classification.intent,
                "domain": result.classification.domain,
                "complexity_score": result.classification.complexity_score,
                "risk_level": result.classification.risk_level,
                "reasoning": result.classification.reasoning,
            }
        
        # Planning
        if result.plan:
            phases["planning"] = {
                "reasoning_summary": result.plan.reasoning_summary,
                "missing_information": result.plan.missing_information,
                "step_count": len(result.plan.plan),
                "steps": [
                    {
                        "step_id": step.step_id,
                        "agent": step.agent,
                        "tool": step.tool,
                        "description": step.description,
                        "parameters": getattr(step, 'parameters', {}),
                        "dependencies": step.dependencies,
                        "requires_replan": step.requires_replan,
                        "pending_goal": step.pending_goal,
                    }
                    for step in result.plan.plan
                ],
            }
        
        # Atomization
        if result.atoms is not None:
            phases["atomization"] = {
                "atoms_created": len(result.atoms),
                "debug": result.atomization_debug,
                "atoms": [
                    {
                        "id": str(atom.id),
                        "tool_name": atom.tool_name,
                        "target_agent": atom.target_agent,
                        "description": atom.description,
                        "tool_arguments": _serialize_uuid(atom.tool_arguments),
                        "risk_level": atom.risk_level,
                        "status": atom.status,
                        "dependencies": [str(d) for d in atom.dependencies],
                        "context_data": _serialize_uuid(atom.context_data),
                    }
                    for atom in result.atoms
                ],
            }
        
        response["phases"] = phases
        
        # Add error info if present
        if result.error:
            response["error"] = result.error
            response["error_type"] = result.error_type
        
        # For early exits, add the response
        if result.early_exit and result.early_exit_response:
            response["early_exit_response"] = result.early_exit_response
        
        # Summary message
        if result.atoms:
            response["message"] = f"Phase 1 complete: {len(result.atoms)} atoms created"
        elif result.early_exit:
            response["message"] = f"Early exit at {result.phase_reached}"
        else:
            response["message"] = f"Stopped at {result.phase_reached}: {result.error}"
        
        return JSONResponse(content=jsonable_encoder(response))
        
    except Exception as e:
        logger.error(f"Phase 1 test failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content=jsonable_encoder({
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            })
        )


@router.post("/classify")
async def classify_only(request: ClassifyOnlyRequest):
    """
    🔍 CLASSIFY ONLY
    
    Run just the classification step (heuristic + semantic).
    No planning or atomization.
    
    Useful for testing classification accuracy.
    """
    try:
        from app.phase1.classifier import heuristic_classify
        from app.phase1.semantic_router import semantic_classify
        
        # Heuristic
        heuristic_result = heuristic_classify(request.message)
        
        # Semantic (if not chat/interrupt)
        semantic_result = None
        if heuristic_result not in ["chat", "interrupt", "system", "error"]:
            classification = semantic_classify(request.message)
            semantic_result = {
                "intent": classification.intent,
                "domain": classification.domain,
                "complexity_score": classification.complexity_score,
                "risk_level": classification.risk_level,
                "reasoning": classification.reasoning,
            }
        
        return {
            "message": request.message,
            "heuristic": heuristic_result,
            "semantic": semantic_result,
            "final_intent": semantic_result["intent"] if semantic_result else heuristic_result,
        }
        
    except Exception as e:
        logger.error(f"Classification failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/plan")
async def plan_only(request: PlanOnlyRequest):
    """
    📋 PLAN ONLY
    
    Run CoT planner to generate execution plan.
    Skips classification (assumes goal/task intent).
    
    Shows raw plan output before atomization.
    """
    try:
        from app.phase1.cot_planner import run_cot_planner
        
        chat_context = request.context or {}
        
        plan = await run_cot_planner(
            request.message,
            workspace_id=request.workspace_id,
            context=chat_context,
        )
        
        return {
            "message": request.message,
            "workspace_id": request.workspace_id,
            "plan": {
                "reasoning_summary": plan.reasoning_summary,
                "missing_information": plan.missing_information,
                "step_count": len(plan.plan),
                "steps": [
                    {
                        "step_id": step.step_id,
                        "agent": step.agent,
                        "tool": step.tool,
                        "description": step.description,
                        "parameters": getattr(step, 'parameters', {}),
                        "dependencies": step.dependencies,
                        "requires_replan": step.requires_replan,
                    }
                    for step in plan.plan
                ],
            },
        }
        
    except Exception as e:
        logger.error(f"Planning failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace/{workspace_id}/agents")
async def get_workspace_agents(workspace_id: str):
    """
    📦 GET WORKSPACE AGENTS
    
    Fetch all agents available in a workspace from Supabase.
    Useful for debugging what the LLM can use in planning.
    """
    try:
        from app.phase1.workspace_cache import WorkspaceCache
        
        agents = await WorkspaceCache.get_agents(workspace_id)
        
        return {
            "workspace_id": workspace_id,
            "agent_count": len(agents),
            "agents": [
                {
                    "name": a["name"],
                    "code": a.get("code"),
                    "category": a.get("category"),
                    "status": a.get("status"),
                }
                for a in agents
            ],
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workspace/{workspace_id}/tools")
async def get_workspace_tools(workspace_id: str):
    """
    🔧 GET WORKSPACE TOOLS
    
    Fetch all tools available in a workspace from Supabase.
    Shows what tool codes the LLM can use in planning.
    """
    try:
        from app.phase1.workspace_cache import WorkspaceCache
        
        tools = await WorkspaceCache.get_tools(workspace_id)
        
        return {
            "workspace_id": workspace_id,
            "tool_count": len(tools),
            "tools": [
                {
                    "code": t.get("code"),
                    "name": t["name"],
                    "connector": t.get("connector"),
                    "operation": t.get("operation"),
                    "risk_level": t.get("risk_level"),
                }
                for t in tools
            ],
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch tools: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/cache/clear")
async def clear_cache():
    """
    🗑️ CLEAR WORKSPACE CACHE
    
    Clear the in-memory cache for agents/tools.
    Use after making changes to Supabase data.
    """
    try:
        from app.phase1.workspace_cache import WorkspaceCache
        
        WorkspaceCache._cache.clear()
        
        return {
            "status": "success",
            "message": "Workspace cache cleared",
        }
        
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
