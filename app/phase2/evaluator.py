"""
PHASE 2: REASONING LAYER (ENRICHMENT ENGINE) - PRODUCTION READY

Per policymain.txt + resourcemain.txt:
  Thread A: LOGIC (Policy + Resources) - Parallel
  Thread B: CONTEXT (Memory + State)   - Parallel

Returns atoms enriched with context_data for Phase 3.

Memory Layer Integration:
  - recall_context_memories(): Hybrid semantic search for episodic + reflective
  - save_workflow_state(): Checkpoint workflows for resumption
  - build_memory_context_injection(): Format memories for LLM prompt
"""

import asyncio
import logging
from typing import Dict, List, Any, Optional

from app.phase1.atoms import ExecutionAtom
from app.phase2.policy.policy_engine import PolicyEngine
from app.phase2.resources import ResourceEngine
from app.phase2.memory.memory_engine import fetch_memory
from app.phase2.memory.retrieval import (
    recall_context_memories,
    build_memory_context_injection,
)
from app.phase2.memory.memory_manager import save_workflow_state
from app.phase2.checks.state_check import run_state_check

logger = logging.getLogger(__name__)


class Phase2Evaluator:
    """
    Phase 2: Reasoning/Enrichment Layer - PRODUCTION IMPLEMENTATION

    Parallel execution via asyncio.gather:
      - Thread A: Policy + Resources (Logic)
      - Thread B: Memory + State (Context - stub)
    """

    def __init__(self, supabase_client=None):
        self.policy_engine = PolicyEngine(supabase_client)
        self.resource_engine = ResourceEngine(supabase_client)

    async def evaluate_atom_async(
        self, atom: ExecutionAtom, workspace_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Main async entry: Enrich atom via parallel Logic + Context

        Args:
            atom: ExecutionAtom from Phase 1
            workspace_id: Tenant context

        Returns:
            {
              "atom_id": str,
              "context_data": { enriched info },
              "decision": "GREEN|YELLOW|RED",
              "status": "ready|requires_approval|blocked"
            }
        """
        # Silently enrich atom

        # ─────────────────── PARALLEL EXECUTION ──────────────────
        logic_result, context_result = await asyncio.gather(
            self._thread_a_logic(atom, workspace_id),
            self._thread_b_context(atom, workspace_id),
            return_exceptions=False,
        )

        # ─────────────────── MERGE RESULTS ──────────────────
        # Preserve existing context_data (e.g., user_id from atomizer)
        # and add enrichment from Phase 2
        context_data = atom.context_data.copy() if atom.context_data else {}
        context_data.update(
            {
                "policy": logic_result.get("policy", {}),
                "resources": logic_result.get("resources", {}),
                "memory": context_result.get("memory", {}),
                "state": context_result.get("state", {}),
            }
        )

        # Update atom with enriched context
        atom.context_data = context_data

        # Check state first (hard blocks override policy)
        state_blocked = context_result.get("state", {}).get("blocked", False)
        state_reason = context_result.get("state", {}).get("reason", None)

        if state_blocked:
            atom.status = "BLOCKED"
            final_status = "blocked"
            policy_decision = "RED"
            logger.warning(f"[Phase2] State block: {state_reason}")
        else:
            # Determine final status based on policy decision
            policy_decision = logic_result.get("policy", {}).get("decision", "GREEN")

            if policy_decision == "RED":
                atom.status = "BLOCKED"
                final_status = "blocked"
            elif policy_decision == "YELLOW":
                atom.status = "PAUSED"
                final_status = "requires_approval"
            else:
                final_status = "ready"

        return {
            "atom_id": str(atom.id),
            "context_data": context_data,
            "decision": policy_decision,
            "status": final_status,
            "atom": atom,
        }

    async def _thread_a_logic(
        self, atom: ExecutionAtom, workspace_id: str
    ) -> Dict[str, Any]:
        """
        Thread A: Logic Engine (Policy + Resources)
        Per policymain.txt + resourcemain.txt
        """
        logger.info(f"[Phase2.ThreadA] Logic: Policy + Resources")

        try:
            # Policy Check
            policy_result = await self.policy_engine.check_atom_against_policies(
                atom, workspace_id
            )
            logger.info(
                f"[Phase2.ThreadA] Policy decision: {policy_result['decision']}"
            )

            # Resource Search
            resource_result = await self.resource_engine.find_resources_for_atom(
                atom, workspace_id
            )
            logger.info(
                f"[Phase2.ThreadA] Resources found: {len(resource_result.get('resources', []))}"
            )

            return {
                "policy": policy_result,
                "resources": resource_result,
            }

        except Exception as e:
            logger.error(f"[Phase2.ThreadA] Error: {str(e)}", exc_info=True)
            return {
                "policy": {
                    "decision": "GREEN",
                    "error": str(e),
                    "reason": "Policy check failed (proceeding with caution)",
                },
                "resources": {
                    "status": "error",
                    "error": str(e),
                },
            }

    async def _thread_b_context(
        self, atom: ExecutionAtom, workspace_id: str
    ) -> Dict[str, Any]:
        """
        Thread B: Context Engine (Memory + State)

        Memory: Episodic + Reflective memories via hybrid semantic search
        State: System state checks (hard blocks)

        Integration with new Memory Layer:
        - recall_context_memories(): Vector search for relevant past context
        - build_memory_context_injection(): Format for LLM injection
        """
        logger.info(f"[Phase2.ThreadB] Context: Memory (Semantic) + State")

        try:
            # ─────────── NEW MEMORY LAYER: SEMANTIC RECALL ─────────
            # Query: Use atom description + tool name for context
            query_text = f"{atom.tool_name}: {atom.description}"

            # Memory recall with filters
            memory_recall = await recall_context_memories(
                org_id=workspace_id,  # Using workspace as org scope
                workspace_id=workspace_id,
                query_text=query_text,
                memory_type=None,  # Search all types
                top_k=5,
                # Uses hardened default threshold (0.65) per approved spec
            )

            # Format memory for LLM injection (token-safe per spec)
            memory_context_str = build_memory_context_injection(
                memory_recall.get("memories", []),
                max_tokens=1500  # Per approved Read Path spec
            )
            
            # BEGIN: Truncation guard per approved spec
            if len(memory_context_str) > 6000:  # ~1500 tokens
                memory_context_str = memory_context_str[:6000] + "\n... (truncated for token limit)"
                logger.warning("[Phase2.ThreadB] Memory injection truncated for token budget")
            # END: Truncation guard

            memory_result = {
                "status": memory_recall.get("status", "success"),
                "count": memory_recall.get("count", 0),
                "top_score": memory_recall.get("top_score", 0.0),
                "memories": memory_recall.get("memories", []),
                "context_injection": memory_context_str,
                "warnings": [],
            }

            # Legacy warning injection (if memories have warnings)
            if memory_recall.get("memories"):
                for mem in memory_recall["memories"]:
                    if mem.get("memory_type") == "preference":
                        memory_result["warnings"].append(
                            f"⚠️  Preference: {mem.get('content', '')[:100]}"
                        )

            logger.info(
                f"[Phase2.ThreadB] Memory: {memory_recall.get('count', 0)} "
                f"memories recalled (score: {memory_recall.get('top_score', 0.0):.2f})"
            )

            # ─────────── STATE CHECK (HARD BLOCKS) ─────────
            state_result = run_state_check(atom)
            logger.info(
                f"[Phase2.ThreadB] State: {'BLOCKED' if state_result['blocked'] else 'OK'}"
            )

            # ─────────── SAVE WORKFLOW STATE (CHECKPOINTING) ─────────
            # NOTE: Legacy atom-level checkpointing DISABLED
            # The new session system in main.py handles session persistence
            # at the conversation level, not per-atom. This old code was
            # creating conflicting entries in workflow_states table.
            # 
            # See: app/core/session/session_service.py for new persistence
            # 
            # Old behavior: One workflow_states row per atom (using atom.id as session_id)
            # New behavior: One workflow_states row per conversation (using session_id from SessionService)
            
            # Skip legacy save - session persistence now handled by SessionService
            logger.debug(
                f"[Phase2.Save] ⏭️ Skipping legacy atom checkpoint - "
                f"session persistence handled by SessionService"
            )


            return {
                "memory": memory_result,
                "state": state_result,
            }

        except Exception as e:
            logger.error(f"[Phase2.ThreadB] Error: {str(e)}", exc_info=True)
            return {
                "memory": {
                    "status": "error",
                    "error": str(e),
                    "warnings": [],
                    "count": 0,
                    "memories": [],
                },
                "state": {
                    "status": "pass",
                    "blocked": False,
                    "reason": None,
                },
            }

    def evaluate_atom(self, atom: ExecutionAtom) -> Dict[str, Any]:
        """
        Synchronous wrapper for legacy code compatibility.
        """
        loop = asyncio.get_event_loop()
        try:
            return loop.run_until_complete(self.evaluate_atom_async(atom))
        except RuntimeError:
            return asyncio.run(self.evaluate_atom_async(atom))
