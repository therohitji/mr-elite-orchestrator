"""
Base Agent Module

Defines the contract all agents must follow and the standardized output schema.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union, Literal
from enum import Enum
from pydantic import BaseModel


# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════

class SkillCategory(str, Enum):
    """Skill categories for UI grouping and routing."""
    COMMUNICATION = "communication"  # Email operations
    CALENDAR = "calendar"            # Calendar events
    TASK = "task"                    # Tasks/todos
    SUMMARIZATION = "summarization"  # Text summarization
    SEARCH = "search"                # Search operations
    GENERAL = "general"              # Fallback


# Mapping from skill IDs to categories
SKILL_TO_CATEGORY: Dict[str, SkillCategory] = {
    # Email skills
    "send_email": SkillCategory.COMMUNICATION,
    "create_draft": SkillCategory.COMMUNICATION,
    "list_threads": SkillCategory.COMMUNICATION,
    "get_thread": SkillCategory.COMMUNICATION,
    # Calendar skills
    "create_event": SkillCategory.CALENDAR,
    "list_events": SkillCategory.CALENDAR,
    "update_event": SkillCategory.CALENDAR,
    "delete_event": SkillCategory.CALENDAR,
    # Summarization skills
    "summarize_text": SkillCategory.SUMMARIZATION,
    "summarize_emails": SkillCategory.SUMMARIZATION,
}


def get_skill_category(skill_id: str) -> SkillCategory:
    """Get the category for a skill ID."""
    return SKILL_TO_CATEGORY.get(skill_id, SkillCategory.GENERAL)


# ═══════════════════════════════════════════════════════════════
# STANDARDIZED OUTPUT SCHEMA
# ═══════════════════════════════════════════════════════════════

class ErrorDetails(BaseModel):
    """Standardized error details."""
    type: str = "UnknownError"
    message: str
    operation: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    retryable: bool = False


class AgentSkillOutput(BaseModel):
    """
    Standardized output from agent handle_skill() methods.
    
    All agents SHOULD return this structure for consistency.
    The `output` field contains skill-specific data (EmailOutput, CalendarOutput, etc.)
    """
    status: Literal["success", "error"]
    skill: str  # The skill ID that was executed
    
    # Main output - skill-specific structured data
    # Should be one of: EmailOutput, CalendarOutput, SummarizerOutput, etc.
    output: Optional[Union[Dict[str, Any], Any]] = None
    
    # Error info (when status == "error")
    error: Optional[ErrorDetails] = None
    
    # Human-readable message for the user
    message: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# BASE AGENT CLASS
# ═══════════════════════════════════════════════════════════════

class BaseAgent(ABC):
    """
    The Contract: Every Agent MUST implement these methods.
    This allows the Executor to treat 'Email' and 'Calendar' exactly the same.
    """

    @abstractmethod
    async def handle_skill(
        self, 
        capability_id: str, 
        args: Dict[str, Any], 
        user_id: str, 
        tool_tokens: Optional[Dict] = None,
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Process the skill and return a standardized response.
        
        Args:
            capability_id: The skill to execute (e.g., 'send_email', 'create_event')
            args: Arguments for the skill
            user_id: User ID for token retrieval
            tool_tokens: Pre-injected OAuth tokens (optional)
            metadata: Additional context (optional)
            
        Returns:
            Should return AgentSkillOutput.model_dump() for consistency:
            {
                "status": "success" | "error",
                "skill": "<skill_id>",
                "output": { ... skill-specific output ... },
                "message": "Human-readable message"
            }
        """
        pass
