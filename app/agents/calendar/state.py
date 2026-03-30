# app/agents/calendar/state.py
from typing import TypedDict, Optional, Dict, Any


class CalendarAgentState(TypedDict):
    user_id: str
    args: Dict[str, Any]  # The user's request (e.g. "Schedule for tomorrow")
    tool_tokens: Dict[str, Any]  # The Google OAuth tokens
    metadata: Dict[str, Any]  # The Skill definition (JSON)

    calendar_context: Optional[str]  # Existing events (if we read the calendar)
    generated_content: Optional[str]  # The LLM's plan or summary
    final_result: Dict[str, Any]  # The output (Event link, etc.)
