# app/phase4/callbacks/memory.py
"""
Memory Callback: Handles storing execution results to memory layer

When Phase 4 successfully executes an atom, this callback stores the
result as an episode memory for future QUERY retrieval.
"""

import logging
from typing import Any, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


def create_memory_handler(
    org_id: str,
    workspace_id: str,
    user_id: Optional[str] = None,
    original_message: Optional[str] = None,
    background_tasks: Optional[Any] = None,  # BackgroundTasks
) -> Callable[[Any, Any], Awaitable[None]]:
    """
    Factory function that creates a memory handler with org/workspace context.
    
    Args:
        org_id: Organization ID for memory storage
        workspace_id: Workspace ID for memory storage
        user_id: Optional user ID for attribution
        original_message: The original user message
        background_tasks: FastAPI BackgroundTasks for async storage
        
    Returns:
        An async callback function that stores results to memory
    """
    
    async def handle_memory(
        atom: Any,  # ExecutionAtom
        dispatcher_result: Any,  # DispatcherResult
    ) -> None:
        """
        Store successful execution result to memory layer.
        
        Args:
            atom: The executed atom
            dispatcher_result: The execution result
        """
        from app.core.supabase_client import save_memory_item
        
        try:
            # Build memory content from execution result
            memory_title = f"Executed: {atom.description[:100]}"
            memory_content = f"User requested: {original_message or 'N/A'}\n"
            memory_content += f"Action: {atom.description}\n"
            memory_content += f"Tool: {atom.tool_name}\n"
            
            # Add output summary if available
            if dispatcher_result.output:
                output_str = str(dispatcher_result.output)
                if len(output_str) > 500:
                    output_str = output_str[:500] + "..."
                memory_content += f"Result: {output_str}"
            
            # Store as episode memory
            if background_tasks:
                # Use background task for async storage (preferred)
                background_tasks.add_task(
                    save_memory_item,
                    org_id=org_id,
                    workspace_id=workspace_id,
                    memory_type="episode",
                    title=memory_title,
                    content=memory_content,
                    source="execution",
                    importance=70,
                    created_by=user_id,
                )
                logger.debug(f"[Memory] Queued storage for atom {atom.id}")
            else:
                # Direct storage (blocking)
                await save_memory_item(
                    org_id=org_id,
                    workspace_id=workspace_id,
                    memory_type="episode",
                    title=memory_title,
                    content=memory_content,
                    source="execution",
                    importance=70,
                    created_by=user_id,
                )
                logger.debug(f"[Memory] Stored result for atom {atom.id}")
                
        except Exception as e:
            logger.warning(f"[Memory] Failed to store for atom {atom.id}: {e}")
    
    return handle_memory
