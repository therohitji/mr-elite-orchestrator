# app/phase4/callbacks/clarification.py
"""
Clarification Callback: Handles creation of clarification sessions

When Phase 4 encounters an atom with target_agent=HUMAN_USER and 
tool_name=user_input_required, this callback creates a clarification
session using ClarificationManager.
"""

import logging
from typing import Any, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


def create_clarification_handler(
    payload: Any,  # InputPayload
) -> Callable[[Any, str], Awaitable[str]]:
    """
    Factory function that creates a clarification handler with access to the request payload.
    
    Args:
        payload: The InputPayload from the /classify endpoint
        
    Returns:
        An async callback function that creates clarification sessions
    """
    from app.core.clarification_manager import (
        ClarificationManager,
        detect_domain_from_atom,
        detect_missing_fields_for_domain,
    )
    
    async def handle_clarification(atom: Any, prompt: str) -> str:
        """
        Create a clarification session for an atom that needs user input.
        
        Args:
            atom: ExecutionAtom requiring clarification
            prompt: The clarification prompt to show to user
            
        Returns:
            clarification_id: ID of the created session
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
            f"[Clarification] Created session {session.clarification_id} "
            f"for domain={domain}, missing={missing_fields}"
        )
        
        return session.clarification_id
    
    return handle_clarification


async def get_clarification_session(clarification_id: str):
    """
    Helper to retrieve a clarification session by ID.
    
    Args:
        clarification_id: The session ID to retrieve
        
    Returns:
        ClarificationSession or None
    """
    from app.core.clarification_manager import ClarificationManager
    
    return ClarificationManager.get_session(clarification_id)
