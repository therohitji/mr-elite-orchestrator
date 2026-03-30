import json
import re

from app.schemas import ClassificationOutput
from app.core.gemini_client import call_gemini
from app.phase1.classifier_prompt import CLASSIFIER_PROMPT


def enforce_intent_rules(parsed: dict, user_input: str = "") -> dict:
    """
    Enforce Phase-1 intent invariants from the PRD.
    Includes edge case handling for TASK vs QUERY distinction.
    """

    complexity = parsed.get("complexity_score")
    intent = parsed.get("intent")

    # RULE 1 (PRD): High complexity can NEVER be a TASK
    if complexity is not None and complexity >= 6:
        parsed["intent"] = "goal"

    # RULE 2: Edge case detection for TASK vs QUERY
    # If LLM classified as QUERY but has action verbs -> might be TASK
    # If LLM classified as TASK but has question words -> might be QUERY

    if user_input:
        user_lower = user_input.lower()

        # Action verbs that indicate TASK
        action_verbs = [
            "get",
            "show",
            "list",
            "send",
            "create",
            "delete",
            "update",
            "schedule",
            "draft",
            "book",
            "find",
            "propose",
            "check",
            "reschedule",
            "cancel",
            "add",
            "remove",
        ]

        # Question words that indicate QUERY
        question_words = [
            "what",
            "when",
            "who",
            "where",
            "why",
            "how",
            "can",
            "could",
            "should",
            "would",
            "is",
            "are",
        ]

        has_action_verb = any(f" {verb} " in f" {user_lower} " for verb in action_verbs)
        has_question_word = any(
            f"{word} " in f"{user_lower} " for word in question_words
        ) or user_lower.endswith("?")

        # Edge case 1: Starts with action verb but ends with question = QUERY
        # "Can you show me my emails?" -> QUERY (asking for capability)
        if has_action_verb and has_question_word and "?" in user_input:
            if user_lower.startswith(("can ", "could ", "would ", "should ")):
                parsed["intent"] = "query"

        # Edge case 2: Has question structure but contains clear action = TASK
        # "Do you want to send the email?" -> TASK (implied send)
        elif has_question_word and intent == "query" and "do you want to" in user_lower:
            parsed["intent"] = "task"

        # Edge case 3: "Tell me what..." = QUERY, not TASK
        # "Tell me what events I have" -> QUERY (asking for info)
        if user_lower.startswith("tell me what") or user_lower.startswith(
            "tell me when"
        ):
            parsed["intent"] = "query"

        # Edge case 4: "Tell me to..." = TASK (with implied action)
        # "Tell me to send email" -> actually means send it (TASK)
        if user_lower.startswith("tell me to "):
            parsed["intent"] = "task"

    return parsed


def semantic_classify(user_input: str) -> ClassificationOutput:
    prompt = CLASSIFIER_PROMPT + f"\nUser Input:\n{user_input}"

    raw_output = call_gemini(prompt)

    try:
        parsed = json.loads(raw_output)
        parsed = enforce_intent_rules(parsed, user_input=user_input)
        
        # Ensure statement.confidence_score is not None (LLM sometimes returns null)
        if "statement" in parsed and isinstance(parsed["statement"], dict):
            if parsed["statement"].get("confidence_score") is None:
                parsed["statement"]["confidence_score"] = 0.0

    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON from Gemini: {raw_output}")

    return ClassificationOutput(**parsed)
