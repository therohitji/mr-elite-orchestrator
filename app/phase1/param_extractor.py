"""
Parameter Extractor for tool arguments.
Extracts specific parameters from user input for different tool types.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from app.core.gemini_client import call_gemini

logger = logging.getLogger(__name__)

PARAM_EXTRACTION_PROMPT = """
You are an expert at extracting specific parameters from user requests.

Given a user's natural language request and a target capability, extract the relevant parameters.

CRITICAL: This extractor ONLY runs for TASK intents (actions), not QUERY (questions).
If the user is asking a question (What, When, Who, Can, Should), this should NOT be called.

INSTRUCTIONS:
1. Parse the user input carefully for all relevant details
2. For calendar events: extract title, attendees (email), date, and time
3. For dates/times: convert "today", "tomorrow", "11 pm" into ISO format datetime
4. Current date is: {current_date}
5. Return ONLY valid JSON with extracted parameters
6. Use null for missing parameters
7. For email addresses: validate they look like email (contain @ symbol)
8. For event updates: Look for event IDs in the format "ID: xxxxx" or event titles that can identify the event

RULES FOR DATE/TIME PARSING:
- "today" = current date
- "tomorrow" = current date + 1 day
- "Monday" = next Monday from current date
- Time like "11 pm" = 23:00 in 24-hour format
- "morning" = 09:00, "afternoon" = 14:00, "evening" = 18:00
- Combine date + time into ISO 8601 format: YYYY-MM-DDTHH:MM:SS
- Assume UTC timezone if not specified

RULES FOR EMAIL EXTRACTION:
- Look for patterns like "john@example.com"
- Handle names: "john" -> might be "john@company.com" if context clear
- Multiple recipients: ["email1@example.com", "email2@example.com"]
- If no clear email, leave as null

RULES FOR EVENT ID EXTRACTION:
- Look for explicit event IDs like "ID: abc123" or "event abc123"
- If user mentions an event title, use the title to identify the event (event_id may be null, title will be used)
- Event IDs are typically alphanumeric strings
- If no event ID found, return null (will be resolved from context or previous steps)

USER REQUEST: {user_input}

CAPABILITY: {capability}

Extract parameters in JSON format based on the capability:

For "event_create":
{{
  "title": "event title or null",
  "start_time": "ISO 8601 datetime or null",
  "end_time": "ISO 8601 datetime or null",
  "attendees": ["email1@example.com", "email2@example.com"] or []
}}

For "event_update":
{{
  "event_id": "event ID string or null",
  "title": "new event title or null",
  "new_start": "ISO 8601 datetime or null (use 'new_start' not 'start_time')",
  "new_end": "ISO 8601 datetime or null (use 'new_end' not 'end_time')",
  "new_title": "new title or null"
}}

For "email_send":
{{
  "to": ["email1@example.com", "email2@example.com"] or [],
  "subject": "subject line or null",
  "body": "email body or null"
}}

Return ONLY the JSON object, no other text.
"""


def extract_event_parameters(user_input: str) -> Dict[str, Any]:
    """
    Extract event parameters from user input.

    Returns:
        Dict with keys: title, start_time, end_time, attendees
    """
    current_date = datetime.now().strftime("%Y-%m-%d")

    prompt = PARAM_EXTRACTION_PROMPT.format(
        current_date=current_date, user_input=user_input, capability="event_create"
    )

    try:
        raw_output = call_gemini(prompt)

        # Clean up JSON if wrapped in markdown
        if "```json" in raw_output:
            raw_output = raw_output.split("```json")[1].split("```")[0]
        elif "```" in raw_output:
            raw_output = raw_output.split("```")[1].split("```")[0]

        params = json.loads(raw_output.strip())

        logger.info(
            f"✅ [PARAM_EXTRACTOR] Extracted event params: title={params.get('title')}, attendees={params.get('attendees')}, start={params.get('start_time')}"
        )

        return params
    except json.JSONDecodeError as e:
        logger.error(f"❌ [PARAM_EXTRACTOR] Failed to parse JSON: {raw_output}")
        return {"title": None, "start_time": None, "end_time": None, "attendees": []}
    except Exception as e:
        logger.error(f"❌ [PARAM_EXTRACTOR] Error extracting params: {e}")
        return {"title": None, "start_time": None, "end_time": None, "attendees": []}


def extract_email_parameters(user_input: str) -> Dict[str, Any]:
    """
    Extract email parameters from user input.

    Returns:
        Dict with keys: to, subject, body
    """
    current_date = datetime.now().strftime("%Y-%m-%d")

    prompt = PARAM_EXTRACTION_PROMPT.format(
        current_date=current_date, user_input=user_input, capability="email_send"
    )

    try:
        raw_output = call_gemini(prompt)

        # Clean up JSON if wrapped in markdown
        if "```json" in raw_output:
            raw_output = raw_output.split("```json")[1].split("```")[0]
        elif "```" in raw_output:
            raw_output = raw_output.split("```")[1].split("```")[0]

        params = json.loads(raw_output.strip())
        
        # Handle list response (safeguard)
        if isinstance(params, list):
            params = params[0] if params else {}
            
        return params
    except json.JSONDecodeError:
        logger.error(f"❌ [PARAM_EXTRACTOR] Failed to parse JSON")
        return {"to": [], "subject": None, "body": None}
    except Exception as e:
        logger.error(f"❌ [PARAM_EXTRACTOR] Error extracting params: {e}")
        return {"to": [], "subject": None, "body": None}


def extract_list_events_parameters(user_input: str) -> Dict[str, Any]:
    """
    Extract list events parameters from user input.

    Returns:
        Dict with keys: date_range (e.g., "today", "tomorrow", "this week")
    """
    # Simple extraction: look for date keywords
    user_lower = user_input.lower()

    if "today" in user_lower:
        date_range = "today"
    elif "tomorrow" in user_lower:
        date_range = "tomorrow"
    elif "this week" in user_lower or "week" in user_lower:
        date_range = "week"
    elif "this month" in user_lower or "month" in user_lower:
        date_range = "month"
    else:
        date_range = "today"  # Default

    logger.info(f"✅ [PARAM_EXTRACTOR] Extracted list params: date_range={date_range}")

    return {"date_range": date_range}


def extract_update_event_parameters(user_input: str) -> Dict[str, Any]:
    """
    Extract event update parameters from user input, including event_id.

    Returns:
        Dict with keys: event_id, new_start, new_end, new_title
    """
    current_date = datetime.now().strftime("%Y-%m-%d")

    prompt = PARAM_EXTRACTION_PROMPT.format(
        current_date=current_date, user_input=user_input, capability="event_update"
    )

    try:
        raw_output = call_gemini(prompt)

        # Clean up JSON if wrapped in markdown
        if "```json" in raw_output:
            raw_output = raw_output.split("```json")[1].split("```")[0]
        elif "```" in raw_output:
            raw_output = raw_output.split("```")[1].split("```")[0]

        params = json.loads(raw_output.strip())

        logger.info(
            f"✅ [PARAM_EXTRACTOR] Extracted update params: event_id={params.get('event_id')}, "
            f"new_start={params.get('new_start')}, new_title={params.get('new_title')}"
        )

        return params
    except json.JSONDecodeError as e:
        logger.error(f"❌ [PARAM_EXTRACTOR] Failed to parse JSON: {raw_output}")
        return {
            "event_id": None,
            "new_start": None,
            "new_end": None,
            "new_title": None,
        }
    except Exception as e:
        logger.error(f"❌ [PARAM_EXTRACTOR] Error extracting update params: {e}")
        return {
            "event_id": None,
            "new_start": None,
            "new_end": None,
            "new_title": None,
        }
