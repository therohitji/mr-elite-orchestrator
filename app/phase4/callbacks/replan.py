# app/phase4/callbacks/replan.py
"""
Re-planning Callback: Handles dynamic re-planning when atoms require it

When Phase 4 encounters an atom with requires_replan=True after successful
execution, this callback invokes the CoT planner to generate new steps
based on the execution result.
"""

import logging
from typing import Any, Optional, Callable, Awaitable, List, Dict

logger = logging.getLogger(__name__)


def create_replan_handler(
    workspace_id: str,
    user_id: Optional[str] = None,
    chat_history: Optional[List[Dict[str, Any]]] = None,
) -> Callable[[Any, Any, str], Awaitable[List[Any]]]:
    """
    Factory function that creates a re-planning handler with workspace context.
    
    Args:
        workspace_id: Workspace ID for Supabase lookups
        user_id: Optional user ID for context
        chat_history: Optional chat history for context
        
    Returns:
        An async callback function that handles re-planning
    """
    
    async def handle_replan(
        atom: Any,  # ExecutionAtom
        dispatcher_result: Any,  # DispatcherResult
        original_message: str,
    ) -> List[Any]:
        """
        Handle re-planning for an atom that requires it.
        
        Args:
            atom: The executed atom with requires_replan=True
            dispatcher_result: The execution result from the dispatcher
            original_message: The original user message
            
        Returns:
            List of new ExecutionAtom objects to add to execution queue
        """
        from app.phase1.cot_planner import run_cot_planner
        from app.phase1.atomizer import AtomizerEngine
        
        # Build re-planning context
        replan_context = {
            "original_goal": atom.original_user_goal or original_message,
            "pending_goal": atom.pending_goal,
            "previous_result": dispatcher_result.output,
            "previous_atom_description": atom.description,
            "is_replan": True,
            "chat_history": chat_history or [],
        }
        
        logger.info(f"[Replan] Invoking CoT planner for atom {atom.id}")
        
        try:
            # Invoke CoT planner with re-planning context
            new_plan = await run_cot_planner(
                user_input=atom.pending_goal or original_message,
                workspace_id=workspace_id,
                context=replan_context,
            )
            
            # Check if planner returned any new steps
            if not new_plan or not new_plan.plan or len(new_plan.plan) == 0:
                reasoning = new_plan.reasoning_summary if new_plan else "No plan returned"
                logger.info(f"[Replan] No further action needed: {reasoning}")
                return []
            
            logger.info(f"[Replan] Generated {len(new_plan.plan)} new step(s)")
            
            # Atomize new plan
            atomizer = AtomizerEngine()
            atomizer_result = await atomizer.atomize(
                new_plan,
                workspace_id=workspace_id,
                user_id=user_id,
                user_input=atom.pending_goal or original_message,
            )
            new_atoms = atomizer_result.get("atoms", [])
            
            # Inject previous result into new atoms' context for chaining
            for new_atom in new_atoms:
                new_atom.context_data["previous_output"] = dispatcher_result.output
                new_atom.original_user_goal = atom.original_user_goal or original_message
            
            logger.info(f"[Replan] Created {len(new_atoms)} new atoms for execution")
            return new_atoms
            
        except Exception as e:
            logger.error(f"[Replan] Failed: {e}", exc_info=True)
            return []  # Re-planning failure shouldn't stop the pipeline
    
    return handle_replan
