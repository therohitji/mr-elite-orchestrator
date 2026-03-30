from typing import TypedDict, Dict, Any, Optional, List


class EmailAgentState(TypedDict):
    # Inputs
    user_id: str
    args: Dict[str, Any]
    metadata: Dict[str, Any]  # The Skill JSON
    tool_tokens: Optional[Dict]

    # Internal State
    email_context: Optional[str]  # Body of the thread we fetched
    generated_content: Optional[str]  # Draft or Summary

    # Output
    final_result: Dict[str, Any]
