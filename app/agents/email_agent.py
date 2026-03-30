"""
Email Agent Implementation
Handles email-related skills and routes to appropriate tools.

Uses standardized output schemas from app/agents/email/models.py
"""

from typing import Dict, Any, Optional, List
import logging

from app.agents.base import AgentSkillOutput, ErrorDetails
from app.agents.email.models import EmailOutput, EmailItem

logger = logging.getLogger(__name__)


def _normalize_recipients(to: Any) -> List[str]:
    """Normalize recipients to a list of strings."""
    if to is None:
        return []
    if isinstance(to, str):
        return [to]
    if isinstance(to, list):
        return to
    return [str(to)]


class EmailAgent:
    """
    Email Agent: Handles all email-related skills.
    Acts as a bridge between Phase 4 dispatcher and GmailTool.
    
    Returns standardized AgentSkillOutput for all operations.
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
        Process email skill.

        Args:
            skill_id: The specific skill to execute (e.g., 'send_email', 'list_threads')
            args: Arguments for the skill
            user_id: User ID for token retrieval
            tool_tokens: Pre-injected tokens (optional)

        Returns:
            Standardized AgentSkillOutput dictionary
        """
        from app.core.tool_factory import ToolFactory

        try:
            # Get or create GmailTool instance
            gmail_tool = await ToolFactory.get_tool("send_email", user_id)
            if not gmail_tool:
                return self._build_error_response(
                    skill_id=skill_id,
                    error_type="InitializationError",
                    message="Failed to initialize Gmail tool. Please ensure your account is connected.",
                )

            # Route to appropriate method based on skill_id
            if skill_id == "send_email":
                return await self._handle_send_email(gmail_tool, skill_id, args)

            elif skill_id == "list_threads":
                return await self._handle_list_threads(gmail_tool, skill_id, args)

            elif skill_id == "get_thread":
                return await self._handle_get_thread(gmail_tool, skill_id, args)

            elif skill_id == "create_draft":
                return await self._handle_create_draft(gmail_tool, skill_id, args)

            else:
                return self._build_error_response(
                    skill_id=skill_id,
                    error_type="UnknownSkillError",
                    message=f"Unknown email skill: {skill_id}",
                )

        except Exception as e:
            logger.error(f"Error in Email Agent: {str(e)}")
            return self._build_error_response(
                skill_id=skill_id,
                error_type="AgentError",
                message=f"Email Agent Error: {str(e)}",
            )

    async def _handle_send_email(
        self, gmail_tool, skill_id: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle send_email skill."""
        to_recipients = args.get("to")
        email_subject = args.get("subject", "")
        email_body = args.get("body") or ""

        if not to_recipients:
            logger.warning("[Email Agent] send_email called with no recipients")

        result = await gmail_tool.send_email(
            to=to_recipients,
            subject=email_subject,
            body=email_body,
            thread_id=args.get("thread_id"),
        )

        # Handle error case
        if "error" in result:
            return self._build_error_response(
                skill_id=skill_id,
                error_type=result["error"].get("type", "SendError"),
                message=result["error"].get("message", "Failed to send email"),
                details=result["error"].get("details"),
            )

        # Build standardized output
        output = EmailOutput(
            message_id=result.get("message_id"),
            to=_normalize_recipients(result.get("to")),
            subject=result.get("subject"),
            body=result.get("body"),
            thread_id=result.get("thread_id"),
            message=result.get("message"),
        )

        return AgentSkillOutput(
            status="success",
            skill=skill_id,
            output=output.model_dump(),
            message=result.get("message", f"✅ Email sent to {to_recipients}"),
        ).model_dump()

    async def _handle_list_threads(
        self, gmail_tool, skill_id: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle list_threads skill."""
        result = await gmail_tool.list_threads(limit=args.get("limit", 5))

        # Handle error case
        if "error" in result:
            return self._build_error_response(
                skill_id=skill_id,
                error_type=result["error"].get("type", "ListError"),
                message=result["error"].get("message", "Failed to list emails"),
                details=result["error"].get("details"),
            )

        # Build standardized output
        emails = result.get("emails", [])
        email_items = []
        for email in emails:
            email_items.append(EmailItem(
                id=email.get("id", ""),
                from_address=email.get("from"),
                subject=email.get("subject"),
                date=email.get("date"),
                snippet=email.get("snippet"),
            ).model_dump(by_alias=True))

        output = EmailOutput(
            emails=email_items,
            account=result.get("account"),
            message=result.get("message"),
        )

        return AgentSkillOutput(
            status="success",
            skill=skill_id,
            output=output.model_dump(),
            message=result.get("message", f"✅ Found {len(emails)} emails"),
        ).model_dump()

    async def _handle_get_thread(
        self, gmail_tool, skill_id: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle get_thread skill."""
        thread_id = args.get("thread_id")
        result = await gmail_tool.get_thread(thread_id=thread_id)

        # Handle error case (get_thread can return dict with error or string)
        if isinstance(result, dict) and "error" in result:
            return self._build_error_response(
                skill_id=skill_id,
                error_type=result["error"].get("type", "FetchError"),
                message=result["error"].get("message", "Failed to fetch thread"),
                details=result["error"].get("details"),
            )

        # get_thread returns a string of thread content
        output = EmailOutput(
            thread_id=thread_id,
            message=result if isinstance(result, str) else str(result),
        )

        return AgentSkillOutput(
            status="success",
            skill=skill_id,
            output=output.model_dump(),
            message=f"✅ Retrieved thread {thread_id}",
        ).model_dump()

    async def _handle_create_draft(
        self, gmail_tool, skill_id: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle create_draft skill."""
        to_recipients = args.get("to")
        draft_subject = args.get("subject", "")
        draft_body = args.get("body") or ""

        if not to_recipients:
            logger.warning("[Email Agent] create_draft called with no recipients")

        result = await gmail_tool.create_draft(
            to=to_recipients,
            subject=draft_subject,
            body=draft_body,
            thread_id=args.get("thread_id"),
        )

        # Handle error case
        if "error" in result:
            return self._build_error_response(
                skill_id=skill_id,
                error_type=result["error"].get("type", "DraftError"),
                message=result["error"].get("message", "Failed to create draft"),
                details=result["error"].get("details"),
            )

        # Build standardized output
        output = EmailOutput(
            draft_id=result.get("draft_id"),
            to=_normalize_recipients(result.get("to")),
            subject=result.get("subject"),
            body=result.get("body"),
            thread_id=result.get("thread_id"),
            message=result.get("message"),
        )

        return AgentSkillOutput(
            status="success",
            skill=skill_id,
            output=output.model_dump(),
            message=result.get("message", f"✅ Draft created for {to_recipients}"),
        ).model_dump()

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
            retryable=self._is_retryable_error(message),
        )

        return AgentSkillOutput(
            status="error",
            skill=skill_id,
            error=error,
            message=message,
        ).model_dump()

    def _is_retryable_error(self, message: str) -> bool:
        """Check if error is retryable."""
        retryable_keywords = ["timeout", "rate limit", "temporary", "try again"]
        return any(kw in message.lower() for kw in retryable_keywords)


email_agent = EmailAgent()
