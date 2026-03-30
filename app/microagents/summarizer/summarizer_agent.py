"""
Summarizer Microagent Implementation
Handles text and email summarization, both directly and when chained after domain agents.

Uses standardized output schemas from app/microagents/summarizer/models.py
"""

from typing import Dict, Any, Optional
import logging

from app.core.gemini_client import call_gemini_text
from app.core.execution.context import global_context
from app.agents.base import AgentSkillOutput, ErrorDetails
from app.microagents.summarizer.models import SummarizerOutput

logger = logging.getLogger(__name__)


class SummarizerAgent:
    """
    Summarizer Microagent: Handles text and email summarization.
    
    Can be invoked:
    1. Directly: User says "Summarize this: [text]"
    2. Chained: After Email Agent via dependencies (e.g., "Summarize my recent emails")
    
    When chained, receives previous atom's output via:
    - metadata["previous_output"] (injected by atomizer)
    - global_context (written by dispatcher after previous atom execution)
    
    Returns standardized AgentSkillOutput for all operations.
    """

    SUMMARIZE_TEXT_PROMPT = """You are a professional summarizer. Provide a concise, clear summary of the following text.

TEXT TO SUMMARIZE:
{text}

Provide:
1. A brief summary (2-3 sentences)
2. Key points (bullet list)

Format your response as:
SUMMARY: [your summary]

KEY POINTS:
- [point 1]
- [point 2]
- [point 3]
"""

    SUMMARIZE_EMAILS_PROMPT = """You are a professional email summarizer. Summarize the following email threads/messages.

EMAILS:
{emails}

For each email or thread, provide:
1. Sender and subject
2. Key message/action items
3. Priority (if determinable)

Then provide an overall summary of all emails.

Format your response clearly with sections for each email and an overall summary at the end.
"""

    async def handle_skill(
        self,
        skill_id: str,
        args: Dict[str, Any],
        user_id: str,
        tool_tokens: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Process summarization skill.

        Args:
            skill_id: The specific skill to execute ('summarize_text' or 'summarize_emails')
            args: Arguments for the skill
            user_id: User ID 
            tool_tokens: Pre-injected tokens (not needed for summarizer)
            metadata: Contains context_data including previous_output from chained atoms

        Returns:
            Standardized AgentSkillOutput dictionary
        """
        logger.info(f"📝 [SUMMARIZER] Handling skill: {skill_id}")
        logger.info(f"📝 [SUMMARIZER] Args: {args}")
        logger.info(f"📝 [SUMMARIZER] Metadata keys: {list(metadata.keys()) if metadata else 'None'}")

        try:
            if skill_id == "summarize_text":
                return await self._summarize_text(args, metadata)
            elif skill_id == "summarize_emails":
                return await self._summarize_emails(args, metadata)
            else:
                return self._build_error_response(
                    skill_id=skill_id,
                    error_type="UnknownSkillError",
                    message=f"Unknown summarizer skill: {skill_id}",
                )

        except Exception as e:
            logger.error(f"❌ [SUMMARIZER] Error: {str(e)}", exc_info=True)
            return self._build_error_response(
                skill_id=skill_id,
                error_type="SummarizerError",
                message=f"Summarizer Error: {str(e)}",
            )

    async def _summarize_text(
        self, args: Dict[str, Any], metadata: Optional[Dict]
    ) -> Dict[str, Any]:
        """Summarize provided text directly."""
        skill_id = "summarize_text"
        
        # Get text from args, or from previous output if chained
        text = args.get("text", "")
        source_type = "text"
        
        if not text and metadata:
            # Check for previous output in metadata (chained execution)
            previous_output = metadata.get("previous_output")
            if previous_output:
                text = self._extract_text_from_output(previous_output)
                logger.info(f"📝 [SUMMARIZER] Using text from previous output")
        
        if not text:
            # Try global context as fallback
            context_key = args.get("context_key", "previous_output")
            text = global_context.get(context_key, "")
            if text:
                logger.info(f"📝 [SUMMARIZER] Using text from global context: {context_key}")
        
        if not text:
            return self._build_error_response(
                skill_id=skill_id,
                error_type="MissingInputError",
                message="No text provided for summarization",
            )

        # Call Gemini for summarization
        prompt = self.SUMMARIZE_TEXT_PROMPT.format(text=text)
        summary = await call_gemini_text(prompt)

        # Build standardized output
        output = SummarizerOutput(
            summary=summary,
            source_type=source_type,
            word_count=len(text.split()),
            summary_word_count=len(summary.split()) if summary else 0,
            message=f"✅ Summarized {len(text.split())} words of text",
        )

        return AgentSkillOutput(
            status="success",
            skill=skill_id,
            output=output.model_dump(),
            message=f"✅ Text summarized successfully",
        ).model_dump()

    async def _summarize_emails(
        self, args: Dict[str, Any], metadata: Optional[Dict]
    ) -> Dict[str, Any]:
        """Summarize email content from args or chained context."""
        skill_id = "summarize_emails"
        
        emails = args.get("emails", [])
        
        # If no emails in args, check for previous output (chained from Email Agent)
        if not emails and metadata:
            previous_output = metadata.get("previous_output")
            if previous_output:
                emails = self._extract_emails_from_output(previous_output)
                logger.info(f"📝 [SUMMARIZER] Extracted {len(emails) if emails else 0} emails from previous output")
        
        if not emails:
            # Try global context as fallback
            context_key = args.get("context_key", "email_list_output")
            emails = global_context.get(context_key, [])
            if emails:
                logger.info(f"📝 [SUMMARIZER] Using {len(emails)} emails from global context")
        
        if not emails:
            return self._build_error_response(
                skill_id=skill_id,
                error_type="MissingInputError",
                message="No emails provided for summarization. Make sure email list was fetched first.",
            )

        # Format emails for prompt
        email_text = self._format_emails_for_prompt(emails)
        
        # Call Gemini for summarization
        prompt = self.SUMMARIZE_EMAILS_PROMPT.format(emails=email_text)
        summary = await call_gemini_text(prompt)

        email_count = len(emails) if isinstance(emails, list) else 1

        # Build standardized output
        output = SummarizerOutput(
            summary=summary,
            source_type="emails",
            source_count=email_count,
            summary_word_count=len(summary.split()) if summary else 0,
            message=f"✅ Summarized {email_count} emails",
        )

        return AgentSkillOutput(
            status="success",
            skill=skill_id,
            output=output.model_dump(),
            message=f"✅ Summarized {email_count} emails",
        ).model_dump()

    def _extract_text_from_output(self, output: Any) -> str:
        """Extract text content from various output formats."""
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            # Try common keys
            for key in ["text", "content", "body", "message", "output"]:
                if key in output:
                    return str(output[key])
            # If has an "output" dict nested
            if "output" in output and isinstance(output["output"], dict):
                return self._extract_text_from_output(output["output"])
        return str(output)

    def _extract_emails_from_output(self, output: Any) -> list:
        """Extract email list from Email Agent output format."""
        if isinstance(output, list):
            return output
        if isinstance(output, dict):
            # Email Agent returns: {"status": "success", "skill": "list_threads", "output": {...}}
            if "output" in output:
                inner = output["output"]
                # inner might be: {"threads": [...], "count": N}
                if isinstance(inner, dict):
                    if "threads" in inner:
                        return inner["threads"]
                    if "emails" in inner:
                        return inner["emails"]
                    if "messages" in inner:
                        return inner["messages"]
                if isinstance(inner, list):
                    return inner
            # Direct threads key
            if "threads" in output:
                return output["threads"]
            if "emails" in output:
                return output["emails"]
        return []

    def _format_emails_for_prompt(self, emails: list) -> str:
        """Format email list for LLM prompt."""
        if not emails:
            return "No emails provided."
        
        formatted = []
        for i, email in enumerate(emails, 1):
            if isinstance(email, dict):
                subject = email.get("subject", email.get("snippet", "No subject"))
                sender = email.get("from", email.get("from_address", email.get("sender", "Unknown")))
                snippet = email.get("snippet", email.get("body", ""))[:200]
                date = email.get("date", email.get("internalDate", ""))
                
                formatted.append(
                    f"Email {i}:\n"
                    f"  From: {sender}\n"
                    f"  Subject: {subject}\n"
                    f"  Date: {date}\n"
                    f"  Preview: {snippet}...\n"
                )
            else:
                formatted.append(f"Email {i}: {str(email)[:200]}...\n")
        
        return "\n".join(formatted)

    def _build_error_response(
        self,
        skill_id: str,
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build standardized error response."""
        error = ErrorDetails(
            type=error_type,
            message=message,
            operation=skill_id,
            details=details,
            retryable=False,
        )

        return AgentSkillOutput(
            status="error",
            skill=skill_id,
            error=error,
            message=message,
        ).model_dump()


# Global instance
summarizer_agent = SummarizerAgent()
