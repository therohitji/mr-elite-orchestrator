import re
from typing import Optional

# ------------------------
# Regex Patterns (Compiled once)
# ------------------------

GREETINGS = re.compile(
    r"\b(hi|hello|hey|good morning|good evening|yo|start)\b",
    re.IGNORECASE,
)

# Action words that indicate a command/task, not just a greeting
# Used to prevent "send email with subject Hello" from being classified as chat
ACTION_WORDS = re.compile(
    r"\b(send|schedule|create|draft|delete|update|list|get|fetch|show|find|search|cancel|reschedule|move|add|remove|summarize|read|check|book|invite|email|meeting|event|calendar|task)\b",
    re.IGNORECASE,
)

ABORT_CMDS = re.compile(
    r"\b(stop|cancel|abort|halt|terminate)\b",
    re.IGNORECASE,
)

SYSTEM_CMDS = re.compile(
    r"^(\/reset|\/config|\/status|\/debug|\/logout)$",
    re.IGNORECASE,
)

# ------------------------
# Heuristic Classifier
# ------------------------

def heuristic_classify(user_input: str) -> Optional[str]:
    """
    Fast, deterministic reflex layer.
    Returns:
        - 'chat'
        - 'interrupt'
        - 'system'
        - 'error'
        - None  -> escalate to LLM
    """

    if not user_input:
        return "error"

    text = user_input.strip().lower()

    # Noise / empty
    if len(text) < 3 and not GREETINGS.search(text):
        return "error"

    # Abort always wins
    if ABORT_CMDS.search(text):
        return "interrupt"

    # System commands
    if SYSTEM_CMDS.match(text):
        return "system"

    # Greetings / chit-chat
    # BUT: If message contains action words, don't treat as chat
    # e.g., "send email with subject Hello" should NOT be chat
    if GREETINGS.search(text) and not ACTION_WORDS.search(text):
        return "chat"

    # No heuristic matched → escalate
    return None
