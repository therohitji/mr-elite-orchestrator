# app/core/clarification_manager.py
"""
Clarification Manager: Session-based state management for multi-turn clarification flows.

Handles:
- Session creation when clarification is needed
- Response parsing via LLM
- Field accumulation across multiple turns
- Enriched message building for Phase 1 re-execution
"""

import uuid
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from app.core.gemini_client import call_gemini_text

logger = logging.getLogger(__name__)


class ClarificationStatus(str, Enum):
    """Status of a clarification session."""
    PENDING = "pending"      # Still collecting information
    COMPLETE = "complete"    # All fields collected
    EXPIRED = "expired"      # Session timed out
    CANCELLED = "cancelled"  # User cancelled


@dataclass
class ClarificationSession:
    """Represents an active clarification conversation."""
    clarification_id: str
    session_id: str               # Links to InputPayload.session_id
    original_message: str         # "send email"
    domain: str                   # "email", "calendar", etc.
    missing_fields: List[str]     # ["to", "subject", "body"]
    collected_data: Dict[str, Any] = field(default_factory=dict)
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    status: ClarificationStatus = ClarificationStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to serializable dict."""
        return {
            "clarification_id": self.clarification_id,
            "session_id": self.session_id,
            "original_message": self.original_message,
            "domain": self.domain,
            "missing_fields": self.missing_fields,
            "collected_data": self.collected_data,
            "conversation_history": self.conversation_history,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


# Domain-specific field definitions
DOMAIN_FIELDS = {
    "email": {
        "required": ["to", "subject", "body"],  # All three needed to send an email
        "optional": ["cc", "bcc"],
        "prompts": {
            "to": "Who should I send this email to? (Please provide email address(es))",
            "subject": "What should the email subject be?",
            "body": "What should the email say?",
        }
    },
    "calendar": {
        "required": ["title", "start_time"],
        "optional": ["end_time", "attendees", "location", "description"],
        "prompts": {
            "title": "What should the meeting be called?",
            "start_time": "When should the meeting be scheduled?",
            "attendees": "Who should attend? (Please provide email address(es))",
        }
    },
    "general": {
        "required": ["details"],
        "optional": [],
        "prompts": {
            "details": "Could you provide more details about what you'd like me to do?",
        }
    }
}


class ClarificationManager:
    """
    Manages clarification sessions in memory.
    
    Responsibilities:
    - Create and store sessions when clarification is needed
    - Parse user responses using LLM
    - Track which fields are still missing
    - Build enriched message for Phase 1 re-execution
    """
    
    # Class-level session store (in-memory)
    _sessions: Dict[str, ClarificationSession] = {}
    
    # Session expiry time (30 minutes)
    SESSION_EXPIRY_MINUTES = 30
    
    @classmethod
    def create_session(
        cls,
        session_id: str,
        original_message: str,
        missing_fields: List[str],
        domain: str = "general",
        initial_prompt: str = None,
        clarification_id: str = None,
    ) -> ClarificationSession:

        """
        Create a new clarification session.
        
        Args:
            session_id: User's session ID from InputPayload
            original_message: Original user request
            missing_fields: Fields that need to be collected
            domain: Domain type (email, calendar, general)
            initial_prompt: First clarifying question
            
        Returns:
            New ClarificationSession object
        """
        # Use provided clarification_id or generate a new one
        if not clarification_id:
            clarification_id = str(uuid.uuid4())

        
        session = ClarificationSession(
            clarification_id=clarification_id,
            session_id=session_id,
            original_message=original_message,
            domain=domain,
            missing_fields=missing_fields.copy(),
            collected_data={},
            conversation_history=[],
        )
        
        # Add initial assistant message if provided
        if initial_prompt:
            session.conversation_history.append({
                "role": "assistant",
                "content": initial_prompt
            })
        
        # Store in memory
        cls._sessions[clarification_id] = session
        
        logger.info(
            f"[CLARIFICATION] Created session {clarification_id} "
            f"for domain={domain}, missing={missing_fields}"
        )
        
        return session
    
    @classmethod
    def get_session(cls, clarification_id: str) -> Optional[ClarificationSession]:
        """
        Retrieve a session by ID.
        
        Returns None if session doesn't exist or is expired.
        """
        session = cls._sessions.get(clarification_id)
        
        if not session:
            return None
        
        # Check expiry
        if cls._is_expired(session):
            session.status = ClarificationStatus.EXPIRED
            logger.info(f"[CLARIFICATION] Session {clarification_id} expired")
            return None
        
        return session
    
    @classmethod
    def _is_expired(cls, session: ClarificationSession) -> bool:
        """Check if session has expired."""
        expiry_time = session.updated_at + timedelta(minutes=cls.SESSION_EXPIRY_MINUTES)
        return datetime.utcnow() > expiry_time
    
    @classmethod
    async def process_response(
        cls,
        clarification_id: str,
        user_response: str,
    ) -> Dict[str, Any]:
        """
        Process a user's response to a clarification question.
        
        Args:
            clarification_id: Session ID
            user_response: User's message
            
        Returns:
            {
                "status": "pending" | "complete" | "error",
                "session": ClarificationSession,
                "next_prompt": str (if pending),
                "enriched_message": str (if complete),
                "error": str (if error)
            }
        """
        session = cls.get_session(clarification_id)
        
        if not session:
            return {
                "status": "error",
                "error": "Clarification session not found or expired. Please start again."
            }
        
        # Add user response to history
        session.conversation_history.append({
            "role": "user",
            "content": user_response
        })
        session.updated_at = datetime.utcnow()
        
        # Parse response using LLM to extract field values
        parsed = await cls._parse_response(
            user_response=user_response,
            missing_fields=session.missing_fields,
            domain=session.domain,
            context=session.collected_data,
        )
        
        logger.info(f"[CLARIFICATION] Parsed response: {parsed}")
        
        # Update collected data with ALL parsed values (not just required fields)
        # This ensures subject/body are captured even though they're optional
        for field_name, value in parsed.items():
            if value:
                session.collected_data[field_name] = value
                logger.info(f"[CLARIFICATION] Collected {field_name}={value}")
                # Remove from missing_fields if it was there (required fields)
                if field_name in session.missing_fields:
                    session.missing_fields.remove(field_name)
        
        # Check if all required fields are collected
        if cls._is_complete(session):
            session.status = ClarificationStatus.COMPLETE
            enriched = cls._build_enriched_message(session)
            
            logger.info(f"[CLARIFICATION] Session complete. Enriched: {enriched}")
            
            return {
                "status": "complete",
                "session": session,
                "enriched_message": enriched,
            }
        
        # Still missing fields - generate next prompt
        next_prompt = await cls._generate_next_prompt(session)
        
        session.conversation_history.append({
            "role": "assistant",
            "content": next_prompt
        })
        
        return {
            "status": "pending",
            "session": session,
            "next_prompt": next_prompt,
            "missing_fields": session.missing_fields,
            "collected_so_far": session.collected_data,
        }
    
    @classmethod
    def _is_complete(cls, session: ClarificationSession) -> bool:
        """Check if all required fields are collected."""
        domain_config = DOMAIN_FIELDS.get(session.domain, DOMAIN_FIELDS["general"])
        required = domain_config.get("required", [])
        
        # Check if all required fields are in collected_data with non-empty values
        for field_name in required:
            value = session.collected_data.get(field_name)
            if not value:
                return False
        
        return True
    
    @classmethod
    async def _parse_response(
        cls,
        user_response: str,
        missing_fields: List[str],
        domain: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Use LLM to parse user response and extract field values.
        
        This is smart parsing - user can provide multiple fields at once.
        """
        # Build field descriptions for prompt
        field_descriptions = []
        for field in missing_fields:
            if domain == "email":
                if field == "to":
                    field_descriptions.append("to: email recipient(s)")
                elif field == "subject":
                    field_descriptions.append("subject: email subject line")
                elif field == "body":
                    field_descriptions.append("body: email body/content")
            elif domain == "calendar":
                if field == "title":
                    field_descriptions.append("title: meeting/event title")
                elif field == "start_time":
                    field_descriptions.append("start_time: when it should happen")
                elif field == "attendees":
                    field_descriptions.append("attendees: email addresses of participants")
            else:
                field_descriptions.append(f"{field}: {field}")
        
        prompt = (
            "Extract information from the user's response to fill in missing fields.\n\n"
            f"Context:\n"
            f"- Domain: {domain}\n"
            f"- Already collected: {json.dumps(context) if context else 'None'}\n"
            f"- Missing fields: {', '.join(missing_fields)}\n\n"
            f'User\'s response: "{user_response}"\n\n'
            "CRITICAL RULES FOR EMAIL:\n"
            "=========================\n"
            "1. ALWAYS generate BOTH subject AND body together. NEVER return subject without body.\n"
            "   If you can create a subject, you MUST also create a body. body=null is FORBIDDEN when subject exists.\n"
            "2. Users will NOT use words like 'subject' or 'body'. Infer them from the user's intent.\n"
            "3. Subject: Create a concise subject line from the user's topic/intent.\n"
            "4. Body: Generate a short, professional email body (2-4 sentences with greeting + sign-off).\n"
            "5. To: Extract email addresses. Look for '@' patterns.\n\n"
            "ABSOLUTE RULE: If you output a subject, you MUST also output a body. No exceptions.\n\n"
            "RULES FOR CALENDAR:\n"
            "===================\n"
            "- Any event description -> extract title + time\n\n"
            "Return ONLY valid JSON, no markdown.\n\n"
            "Example 1:\n"
            'User: "john@example.com about the meeting"\n'
            'Output: {"to": "john@example.com", "subject": "Meeting", "body": "Hi,\\nI wanted to discuss the upcoming meeting. Please let me know a convenient time.\\nThanks"}\n\n'
            "Example 2:\n"
            'User: "ask him for a cricket match on sunday"\n'
            'Output: {"subject": "Cricket Match on Sunday?", "body": "Hey,\\nWould you like to play cricket this Sunday? Let me know if you are free!\\nCheers"}\n\n'
            "Example 3:\n"
            'User: "tell him about the project update"\n'
            'Output: {"subject": "Project Update", "body": "Hi,\\nI wanted to share a quick project update with you. Let me know if you would like to discuss.\\nThanks"}\n\n'
            "Example 4:\n"
            'User: "to ja7746@gmail.com ask him for a cricket match on sunday"\n'
            'Output: {"to": "ja7746@gmail.com", "subject": "Cricket Match on Sunday?", "body": "Hey,\\nWould you like to play a cricket match this Sunday? Let me know if you are available!\\nCheers"}\n'
        )
        
        try:
            result = await call_gemini_text(prompt)
            
            # Clean up response (remove markdown if present)
            result = result.strip()
            if result.startswith("```"):
                result = result.split("```")[1]
                if result.startswith("json"):
                    result = result[4:]
            result = result.strip()
            
            parsed = json.loads(result)
            
            # Filter out null values
            return {k: v for k, v in parsed.items() if v is not None}
            
        except json.JSONDecodeError as e:
            logger.warning(f"[CLARIFICATION] Failed to parse LLM response: {e}")
            # Fallback: treat entire response as first missing field
            if missing_fields:
                return {missing_fields[0]: user_response}
            return {}
        except Exception as e:
            logger.error(f"[CLARIFICATION] Error in parse_response: {e}")
            return {}
    
    @classmethod
    async def _generate_next_prompt(cls, session: ClarificationSession) -> str:
        """Generate a natural clarifying question for the next missing field."""
        if not session.missing_fields:
            return "I have all the information I need!"
        
        next_field = session.missing_fields[0]
        domain_config = DOMAIN_FIELDS.get(session.domain, DOMAIN_FIELDS["general"])
        
        # Check for predefined prompt
        predefined = domain_config.get("prompts", {}).get(next_field)
        if predefined:
            return f"🤔 {predefined}"
        
        # Generate custom prompt with LLM
        prompt = f"""Generate a friendly, concise question to ask the user for the following information:

Field needed: {next_field}
Domain: {session.domain}
Original request: "{session.original_message}"
Already collected: {json.dumps(session.collected_data) if session.collected_data else "Nothing yet"}

Generate a single, clear question. Be friendly and helpful.
Return ONLY the question text, no explanations.
"""
        
        try:
            question = await call_gemini_text(prompt)
            return f"🤔 {question.strip()}"
        except Exception as e:
            logger.warning(f"[CLARIFICATION] Error generating prompt: {e}")
            return f"🤔 Could you please provide the {next_field.replace('_', ' ')}?"
    
    @classmethod
    def _build_enriched_message(cls, session: ClarificationSession) -> str:
        """
        Build an enriched user message with all collected information.
        
        This will be sent to Phase 1 for re-planning.
        """
        original = session.original_message
        collected = session.collected_data
        
        if session.domain == "email":
            parts = [original]
            if "to" in collected:
                parts.append(f"to {collected['to']}")
            if "subject" in collected:
                parts.append(f"with subject \"{collected['subject']}\"")
            if "body" in collected:
                parts.append(f"saying \"{collected['body']}\"")
            return " ".join(parts)
        
        elif session.domain == "calendar":
            parts = [original]
            if "title" in collected:
                parts.append(f"called \"{collected['title']}\"")
            if "start_time" in collected:
                parts.append(f"at {collected['start_time']}")
            if "attendees" in collected:
                attendees = collected["attendees"]
                if isinstance(attendees, list):
                    attendees = ", ".join(attendees)
                parts.append(f"with {attendees}")
            return " ".join(parts)
        
        else:
            # Generic: append details as JSON context
            return f"{original}. Details: {json.dumps(collected)}"
    
    @classmethod
    async def parse_user_response(
        cls,
        user_response: str,
        missing_fields: List[str],
        collected_data: Dict[str, Any],
        domain: str,
    ) -> Dict[str, Any]:
        """
        Public helper to parse user response without requiring in-memory session.
        
        Used by the persistent session system to extract field values from user messages.
        
        Args:
            user_response: The user's message
            missing_fields: Fields still needed
            collected_data: Data already collected
            domain: Domain type (email, calendar, general)
            
        Returns:
            Dict with keys:
                - status: "success" or "error"
                - field: The field that was extracted (first one found)
                - value: The extracted value
                - all_extracted: Dict of all fields extracted
                - response: Acknowledgment message
                - error: Error message if status is "error"
        """
        try:
            # Use internal parse method
            extracted = await cls._parse_response(
                user_response=user_response,
                missing_fields=missing_fields,
                domain=domain,
                context=collected_data,
            )
            
            if not extracted:
                return {
                    "status": "error",
                    "error": "Could not extract any information from your response. Please try again.",
                }
            
            # Get first extracted field
            field = list(extracted.keys())[0]
            value = extracted[field]
            
            # Generate acknowledgment
            response = f"Got it, {field}: {value}"
            
            return {
                "status": "success",
                "field": field,
                "value": value,
                "all_extracted": extracted,
                "response": response,
            }
            
        except Exception as e:
            logger.error(f"[CLARIFICATION] parse_user_response error: {e}")
            return {
                "status": "error",
                "error": str(e),
            }
    
    @classmethod
    def get_field_prompt(cls, domain: str, field: str) -> str:
        """
        Get the prompt for a specific field in a domain.
        
        Used by the persistent session system to generate prompts.
        
        Args:
            domain: Domain type (email, calendar, general)
            field: Field name to get prompt for
            
        Returns:
            The prompt string for that field
        """
        domain_def = DOMAIN_FIELDS.get(domain, DOMAIN_FIELDS["general"])
        prompts = domain_def.get("prompts", {})
        
        if field in prompts:
            return prompts[field]
        
        # Fallback prompts
        return f"Could you please provide the {field}?"
    
    @classmethod
    def cleanup_expired(cls) -> int:

        """Remove expired sessions. Returns count of removed sessions."""
        expired_ids = [
            cid for cid, session in cls._sessions.items()
            if cls._is_expired(session)
        ]
        
        for cid in expired_ids:
            del cls._sessions[cid]
        
        if expired_ids:
            logger.info(f"[CLARIFICATION] Cleaned up {len(expired_ids)} expired sessions")
        
        return len(expired_ids)
    
    @classmethod
    def get_session_count(cls) -> int:
        """Get total number of active sessions."""
        return len(cls._sessions)


# Convenience function for detecting domain from atom
def detect_domain_from_atom(atom) -> str:
    """Detect clarification domain from an ExecutionAtom."""
    tool_name = getattr(atom, "tool_name", "").lower()
    target_agent = getattr(atom, "target_agent", "").lower()
    description = getattr(atom, "description", "").lower()
    prompt = atom.tool_arguments.get("prompt", "").lower() if atom.tool_arguments else ""
    
    # Check text content for keywords
    full_text = f"{tool_name} {target_agent} {description} {prompt}"
    
    if "email" in full_text or "gmail" in full_text or "mail" in full_text:
        return "email"
    elif "calendar" in full_text or "event" in full_text or "meeting" in full_text or "schedule" in full_text:
        return "calendar"
    else:
        return "general"


def detect_missing_fields_for_domain(domain: str, tool_arguments: Dict) -> List[str]:
    """Determine which REQUIRED fields are missing based on domain.
    
    NOTE: Only checks required fields, NOT optional ones (like cc/bcc for email).
    This prevents the system from repeatedly asking for optional parameters.
    """
    domain_config = DOMAIN_FIELDS.get(domain, DOMAIN_FIELDS["general"])
    
    # ONLY check required fields, NOT optional ones like cc/bcc
    required_fields = domain_config["required"]
    
    missing = []
    for field in required_fields:
        value = tool_arguments.get(field)
        if value in (None, "", [], {}, [""]):
            missing.append(field)
    
    return missing if missing else ["details"]
