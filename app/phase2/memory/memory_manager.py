"""
PHASE 2: MEMORY MANAGER - INGESTION PIPELINE
============================================================================
Purpose: Convert completed workflows into episodic + reflective memories
Strategy: The "Zipper Method" - compress 50+ messages into 1-2 insights
============================================================================
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import UUID

from app.core.gemini_client import call_gemini_embedding
from app.core.supabase_client import supabase_client, supabase_service_client

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================


class MemoryInsight(BaseModel):
    """Single extracted memory insight from a workflow"""

    memory_type: Literal["episode", "semantic", "preference", "entity_note"]
    title: str
    content: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    importance: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)


class MemoryExtraction(BaseModel):
    """Complete extraction result from workflow analysis"""

    episode: Optional[MemoryInsight] = None
    semantic_facts: List[MemoryInsight] = []
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    has_errors: bool = False


# ============================================================================
# INGESTION FUNCTION
# ============================================================================


async def ingest_memory_from_workflow(
    org_id: str,
    workspace_id: str,
    workflow_id: str,
    workflow_data: Dict,
    user_id: Optional[str] = None,
) -> Dict:
    """
    Main ingestion function: Save workflow as memory

    Args:
        org_id: Organization UUID
        workspace_id: Workspace UUID
        workflow_id: Unique workflow identifier
        workflow_data: {
            "status": "completed|failed",
            "goal": str,
            "actions": list[tool calls],
            "outcome": str,
            "feedback": str optional (user sentiment)
        }
        user_id: User who triggered workflow

    Returns:
        {
            "status": "success|failed",
            "memories_created": int,
            "errors": list
        }
    """
    logger.info(f"[Memory] Ingesting workflow {workflow_id}")

    # BEGIN: Eligibility guards per approved Memory Governance spec
    # Never learn from: clarifications, rejections, partial executions, policy violations
    
    workflow_status = workflow_data.get("status", "")
    if workflow_status != "completed":
        logger.info(f"[Memory] Skipping non-completed workflow (status: {workflow_status})")
        return {"status": "skipped", "reason": "not_completed", "memories_created": 0}
    
    if workflow_data.get("has_rejections", False):
        logger.info(f"[Memory] Skipping workflow with rejections")
        return {"status": "skipped", "reason": "has_rejections", "memories_created": 0}
    
    if workflow_data.get("has_clarifications", False):
        logger.info(f"[Memory] Skipping workflow with pending clarifications")
        return {"status": "skipped", "reason": "has_clarifications", "memories_created": 0}
    
    # Check for successful actions (at least one must have succeeded)
    actions = workflow_data.get("actions", [])
    success_count = sum(1 for a in actions if a.get("status") == "SUCCESS")
    if success_count == 0:
        logger.info(f"[Memory] Skipping workflow with no successful actions")
        return {"status": "skipped", "reason": "no_success", "memories_created": 0}
    # END: Eligibility guards

    try:
        # Step 1: Analyze workflow (extract insights)
        extraction = await _analyze_workflow(workflow_data)

        if not extraction:
            logger.warning(f"[Memory] No insights extracted from {workflow_id}")
            return {
                "status": "failed",
                "memories_created": 0,
                "errors": ["No insights extracted"],
            }

        # Step 2: Create memory records
        memory_ids = []

        # Create episode memory
        if extraction.episode:
            mem_id = await _save_memory_item(
                org_id=org_id,
                workspace_id=workspace_id,
                insight=extraction.episode,
                source="workflow",
                created_by=user_id,
            )
            if mem_id:
                memory_ids.append(mem_id)

        # Create semantic facts
        for fact in extraction.semantic_facts:
            mem_id = await _save_memory_item(
                org_id=org_id,
                workspace_id=workspace_id,
                insight=fact,
                source="workflow",
                created_by=user_id,
            )
            if mem_id:
                memory_ids.append(mem_id)

        # Step 3: Embed all new memories
        await _embed_memories(org_id, memory_ids)

        # Step 4: De-duplicate (mark similar old memories as deprecated)
        dedup_count = await _dedup_memories(org_id, memory_ids)

        logger.info(
            f"[Memory] Workflow {workflow_id} ingested: "
            f"{len(memory_ids)} memories created, {dedup_count} deprecated"
        )

        return {
            "status": "success",
            "memories_created": len(memory_ids),
            "deprecated": dedup_count,
            "memory_ids": memory_ids,
        }

    except Exception as e:
        logger.error(f"[Memory] Ingestion failed for {workflow_id}: {e}")
        return {"status": "failed", "memories_created": 0, "errors": [str(e)]}


# ============================================================================
# STEP 1: ANALYZE WORKFLOW (COMPRESSION)
# ============================================================================


async def _analyze_workflow(workflow_data: Dict) -> Optional[MemoryExtraction]:
    """
    Use LLM to compress workflow into insights
    The "Zipper Method": 50+ messages → 1-2 clean insights
    """

    # Build analysis prompt
    prompt = f"""Analyze this completed workflow and extract memories.

WORKFLOW DATA:
Goal: {workflow_data.get('goal', 'Unknown')}
Status: {workflow_data.get('status', 'Unknown')}
Actions: {json.dumps(workflow_data.get('actions', []), indent=2)}
Outcome: {workflow_data.get('outcome', 'Unknown')}
User Feedback: {workflow_data.get('feedback', 'No feedback')}

TASK:
1. Extract ONE episode memory (what happened, compressed)
2. Extract 0-10 semantic facts (stable rules/preferences learned)
3. Rate overall sentiment (-1.0 = very negative, 1.0 = very positive)
4. Did any errors occur?

OUTPUT JSON FORMAT:
{{
  "episode": {{
    "memory_type": "episode",
    "title": "short title",
    "content": "paragraph describing what happened, outcomes, key constraints",
    "importance": 0-100,
    "confidence": 0.0-1.0
  }},
  "semantic_facts": [
    {{
      "memory_type": "semantic",
      "title": "fact title",
      "content": "stable rule or preference learned",
      "entity_type": "company|candidate|vendor|project|deal|null",
      "entity_id": "uuid or null",
      "importance": 0-100,
      "confidence": 0.0-1.0
    }}
  ],
  "sentiment_score": -1.0 to 1.0,
  "has_errors": true|false
}}

Respond ONLY with valid JSON, no markdown."""

    try:
        # Call Gemini to analyze (or your chosen LLM)
        from app.core.gemini_client import call_gemini_analysis

        response = await call_gemini_analysis(prompt)

        # Parse JSON response
        extraction_dict = json.loads(response)
        extraction = MemoryExtraction(**extraction_dict)
        return extraction

    except Exception as e:
        logger.error(f"[Memory] Workflow analysis failed: {e}")
        return None


# ============================================================================
# STEP 2: SAVE MEMORY ITEM
# ============================================================================


async def _save_memory_item(
    org_id: str,
    workspace_id: str,
    insight: MemoryInsight,
    source: str,
    created_by: Optional[str] = None,
) -> Optional[str]:
    """
    Insert memory item into database
    """
    if not supabase_client:
        logger.error("[Memory] Supabase client not initialized")
        return None

    try:
        # Validate and sanitize created_by
        # If it's a MongoDB ObjectId (24 char hex) or invalid UUID, set to None
        sanitized_created_by = None
        if created_by:
            try:
                # Check if it's a valid UUID
                UUID(created_by)
                sanitized_created_by = created_by
            except (ValueError, AttributeError):
                # Not a valid UUID, likely MongoDB ObjectId
                logger.warning(
                    f"[Memory] created_by '{created_by}' is not a valid UUID, skipping"
                )
                sanitized_created_by = None

        result = (
            supabase_client.table("memory_items")
            .insert(
                {
                    "org_id": org_id,
                    "workspace_id": workspace_id,
                    "created_by": sanitized_created_by,
                    "memory_type": insight.memory_type,
                    "entity_type": insight.entity_type,
                    "entity_id": insight.entity_id,
                    "title": insight.title,
                    "content": insight.content,
                    "source": source,
                    "importance": insight.importance,
                    "status": "active",
                    # BEGIN: Audit metadata per approved Governance spec
                    "raw_payload": {
                        "confidence": insight.confidence,
                        "extracted_at": datetime.utcnow().isoformat(),
                        "extraction_model": "gemini-1.5-pro",
                        "eligibility_signals": {
                            "phase4_success": True,
                            "no_clarifications": True,
                            "no_approvals_pending": True,
                        }
                    },
                    # END: Audit metadata
                }
            )
            .execute()
        )

        if result.data and len(result.data) > 0:
            memory_id = result.data[0]["id"]
            logger.info(f"[Memory] Saved memory item: {memory_id}")
            return memory_id
        else:
            logger.error(f"[Memory] Failed to insert memory: {result}")
            return None

    except Exception as e:
        logger.error(f"[Memory] Error saving memory item: {e}")
        return None


# ============================================================================
# STEP 3: EMBED MEMORIES (VECTORIZATION)
# ============================================================================


async def _embed_memories(org_id: str, memory_ids: List[str]) -> Dict:
    """
    Generate embeddings for newly created memories
    Batch processing for efficiency
    """
    if not memory_ids:
        return {"status": "success", "embedded": 0}

    if not supabase_client:
        logger.error("[Memory] Supabase client not initialized")
        return {"status": "failed", "errors": ["Supabase not initialized"]}

    try:
        # Fetch memory content
        memories = (
            supabase_client.table("memory_items")
            .select("id, title, content")
            .in_("id", memory_ids)
            .execute()
        )

        if not memories.data:
            return {"status": "success", "embedded": 0}

        embedded_count = 0
        errors = []

        # Embed in batches (e.g., 10 at a time)
        for i in range(0, len(memories.data), 10):
            batch = memories.data[i : i + 10]

            for mem in batch:
                try:
                    # Generate embedding (1536-dim for text-embedding-3-small)
                    text_to_embed = f"{mem['title']} {mem['content']}"
                    embedding = await call_gemini_embedding(text_to_embed)

                    if not embedding:
                        logger.warning(f"[Memory] Embedding failed for {mem['id']}")
                        continue

                    # Save embedding to memory_vectors
                    result = (
                        supabase_client.table("memory_vectors")
                        .insert(
                            {
                                "memory_id": mem["id"],
                                "org_id": org_id,
                                "embedding": embedding,
                                "embedding_model": "text-embedding-3-small",
                            }
                        )
                        .execute()
                    )

                    if result.data:
                        embedded_count += 1
                        logger.info(f"[Memory] Embedded: {mem['id']}")
                    else:
                        errors.append(f"Failed to save embedding for {mem['id']}")

                except Exception as e:
                    errors.append(f"Embedding error for {mem['id']}: {str(e)}")
                    logger.error(f"[Memory] Embedding error: {e}")

            # Small delay between batches
            if i + 10 < len(memories.data):
                await asyncio.sleep(0.5)

        return {
            "status": "success" if embedded_count == len(memories.data) else "partial",
            "embedded": embedded_count,
            "errors": errors if errors else [],
        }

    except Exception as e:
        logger.error(f"[Memory] Batch embedding failed: {e}")
        return {"status": "failed", "errors": [str(e)]}


# ============================================================================
# STEP 4: DE-DUPLICATION (CONSOLIDATION)
# ============================================================================


async def _dedup_memories(org_id: str, memory_ids: List[str]) -> int:
    """
    Deprecate old memories if new ones are similar (similarity > 0.92)
    """
    if not supabase_client or not memory_ids:
        return 0

    try:
        dedup_count = 0

        for memory_id in memory_ids:
            # Call RPC to deprecate similar memories
            result = supabase_client.rpc(
                "dedup_memories",
                {
                    "p_org_id": org_id,
                    "p_memory_id": memory_id,
                    "p_similarity_threshold": 0.92,
                },
            ).execute()

            if result.data:
                count = result.data if isinstance(result.data, int) else 0
                dedup_count += count

        logger.info(f"[Memory] De-duplication: {dedup_count} old memories deprecated")
        return dedup_count

    except Exception as e:
        logger.warning(f"[Memory] De-duplication failed: {e}")
        return 0


# ============================================================================
# WORKFLOW STATE CHECKPOINTING (FOR RESUMPTION)
# ============================================================================


async def save_workflow_state(
    workspace_id: str,
    user_id: Optional[str],
    session_id: str,
    current_node: str,
    state_data: Dict,
    status: Literal["running", "paused", "completed", "failed"] = "running",
    priority: str = "medium",
    last_node: Optional[str] = None,
) -> Optional[str]:
    """
    Save workflow execution state for resumption (LangGraph checkpoint)

    Mechanism:
    - Queries workflow_states table with workspace_id + session_id
    - If exists: UPDATE the row with new state data
    - If not exists: INSERT new row
    - Stores: current_node, state_data (JSONB), status, priority, timestamps
    """
    if not supabase_client:
        logger.error("[Memory.Save] Supabase client not initialized")
        return None

    try:
        logger.info(
            f"[Memory.Save] Starting checkpoint save: workspace_id={workspace_id}, session_id={str(session_id)[:8]}..."
        )

        # Check if state already exists (upsert pattern)
        existing = (
            supabase_client.table("workflow_states")
            .select("session_id")
            .eq("workspace_id", workspace_id)
            .eq("session_id", session_id)
            .execute()
        )

        now = datetime.utcnow().isoformat()

        if existing.data and len(existing.data) > 0:
            # Update existing
            logger.info(
                f"[Memory.Save] Found existing state record. Performing UPDATE..."
            )
            result = (
                supabase_service_client.table("workflow_states")
                .update(
                    {
                        "user_id": user_id,
                        "current_node": current_node,
                        "last_node": last_node,
                        "state_data": state_data,
                        "status": status,
                        "priority": priority,
                        "updated_at": now,
                    }
                )
                .eq("session_id", session_id)
                .execute()
            )
        else:
            # Insert new
            logger.info(
                f"[Memory.Save] No existing record. Performing INSERT with fields: session_id, workspace_id, current_node, state_data, status, priority..."
            )
            result = (
                supabase_service_client.table("workflow_states")
                .insert(
                    {
                        "session_id": session_id,
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "current_node": current_node,
                        "last_node": last_node,
                        "state_data": state_data,
                        "status": status,
                        "priority": priority,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                .execute()
            )

        if result.data and len(result.data) > 0:
            sid = result.data[0].get("session_id")
            logger.info(
                f"[Memory.Save] ✅ SUCCESS: Workflow state checkpoint saved. session_id={str(sid)[:8]}..."
            )
            return sid
        else:
            logger.error(
                f"[Memory.Save] ❌ FAILED: Save returned no data. Response: {result}"
            )
            return None

    except Exception as e:
        logger.error(
            f"[Memory.Save] ❌ ERROR during checkpoint save: {type(e).__name__}: {str(e)}"
        )
        logger.debug(
            f"[Memory.Save] Full error context: workspace_id={workspace_id}, session_id={str(session_id)[:8]}..."
        )
        return None


async def load_workflow_state(workspace_id: str, session_id: str) -> Optional[Dict]:
    """
    Load saved workflow state for resumption
    """
    if not supabase_client:
        logger.error("[Memory] Supabase client not initialized")
        return None

    try:

        result = (
            supabase_client.table("workflow_states")
            .select("state_data, current_node, last_node, status, priority, updated_at")
            .eq("workspace_id", workspace_id)
            .eq("session_id", session_id)
            .execute()
        )

        if result.data and len(result.data) > 0:
            return result.data[0]
        else:
            logger.info(f"[Memory] No saved state found for {session_id}")
            return None

    except Exception as e:
        logger.error(f"[Memory] Error loading workflow state: {e}")
        return None
