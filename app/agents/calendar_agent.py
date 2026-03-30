"""
Calendar Agent Implementation
Handles calendar-related skills and routes to appropriate tools.

Uses standardized output schemas from app/agents/calendar/models.py
"""

from typing import Dict, Any, Optional, List
import logging
import datetime

from app.agents.base import AgentSkillOutput, ErrorDetails
from app.agents.calendar.models import CalendarOutput, CalendarEventOutput

logger = logging.getLogger(__name__)


def _normalize_attendees(attendees: Any) -> List[str]:
    """Normalize attendees to a list of strings."""
    if attendees is None:
        return []
    if isinstance(attendees, str):
        return [attendees]
    if isinstance(attendees, list):
        return attendees
    return [str(attendees)]


class CalendarAgent:
    """
    Calendar Agent: Handles all calendar-related skills.
    Acts as a bridge between Phase 4 dispatcher and CalendarTool.
    
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
        Process calendar skill.

        Args:
            skill_id: The specific skill to execute
            args: Arguments for the skill
            user_id: User ID for token retrieval
            tool_tokens: Pre-injected tokens (optional)

        Returns:
            Standardized AgentSkillOutput dictionary
        """
        from app.core.tool_factory import ToolFactory

        logger.info(
            f"📅 [CALENDAR_AGENT] Received: skill_id={skill_id}, user_id={user_id}, tokens={'present' if tool_tokens else 'missing'}"
        )

        try:
            # Extract the actual operation from tool_arguments
            # skill_id might be the full Supabase name like "Calendar – Create Event"
            # but we need just the operation like "create_event"
            operation = self._normalize_operation(args.get("operation", skill_id))

            # Get or create CalendarTool instance
            calendar_tool = await ToolFactory.get_tool(operation, user_id)
            if not calendar_tool:
                return self._build_error_response(
                    skill_id=operation,
                    error_type="InitializationError",
                    message="Failed to initialize Calendar tool. Please ensure your account is connected.",
                )

            # Route to appropriate method based on operation
            if operation == "create_event":
                return await self._handle_create_event(calendar_tool, operation, args)

            elif operation == "list_events":
                return await self._handle_list_events(calendar_tool, operation, args)

            elif operation == "update_event":
                return await self._handle_update_event(calendar_tool, operation, args, metadata)

            else:
                return self._build_error_response(
                    skill_id=operation,
                    error_type="UnknownSkillError",
                    message=f"Unknown calendar skill: {skill_id}",
                )

        except Exception as e:
            logger.error(f"Error in Calendar Agent: {str(e)}")
            return self._build_error_response(
                skill_id=skill_id,
                error_type="AgentError",
                message=f"Calendar Agent Error: {str(e)}",
            )

    def _normalize_operation(self, operation: str) -> str:
        """Normalize operation name to standard format."""
        operation = operation.lower()
        
        if operation in ["create_event", "list_events", "update_event", "delete_event"]:
            return operation
        
        # Try to extract from full name
        if "create" in operation:
            return "create_event"
        elif "list" in operation:
            return "list_events"
        elif "update" in operation:
            return "update_event"
        elif "delete" in operation:
            return "delete_event"
        
        return operation

    async def _handle_create_event(
        self, calendar_tool, operation: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle create_event skill."""
        title = args.get("title", "Team Meeting")
        start_time = args.get("start_time")
        end_time = args.get("end_time")
        attendees = args.get("attendees", [])

        # If start_time is missing, default to now + 1 hour
        if not start_time:
            now = datetime.datetime.now(datetime.timezone.utc)
            start_dt = now + datetime.timedelta(hours=1)
            start_time = start_dt.isoformat()

        # If no end_time, default to 30 minutes after start
        if not end_time and start_time:
            try:
                start_dt = datetime.datetime.fromisoformat(
                    start_time.replace("Z", "+00:00")
                )
                end_dt = start_dt + datetime.timedelta(minutes=30)
                end_time = end_dt.isoformat()
            except Exception as e:
                logger.error(f"Error parsing start_time: {e}")
                end_time = start_time

        result = await calendar_tool.create_event(
            title=title,
            start_time=start_time,
            end_time=end_time,
            attendees=_normalize_attendees(attendees),
        )

        # Handle error case
        if "error" in result:
            return self._build_error_response(
                skill_id=operation,
                error_type=result["error"].get("type", "CreateError"),
                message=result["error"].get("message", "Failed to create event"),
                details=result["error"].get("details"),
            )

        # Build standardized output
        event_output = CalendarEventOutput(
            event_id=result.get("event_id"),
            title=result.get("title"),
            start_time=result.get("start_time"),
            end_time=result.get("end_time"),
            timezone=result.get("timezone"),
            attendees=result.get("attendees"),
            resource_link=result.get("link"),
        )

        output = CalendarOutput(
            event=event_output,
            timezone=result.get("timezone"),
            message=result.get("message"),
        )

        return AgentSkillOutput(
            status="success",
            skill=operation,
            output=output.model_dump(),
            message=result.get("message", f"✅ Event '{title}' created"),
        ).model_dump()

    async def _handle_list_events(
        self, calendar_tool, operation: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle list_events skill."""
        date_range = args.get("date_range", "today")
        result = await calendar_tool.list_events(date_range=date_range)

        # Handle error case
        if "error" in result:
            return self._build_error_response(
                skill_id=operation,
                error_type=result["error"].get("type", "ListError"),
                message=result["error"].get("message", "Failed to list events"),
                details=result["error"].get("details"),
            )

        # Build standardized output
        events = result.get("events", [])
        event_outputs = []
        for event in events:
            event_outputs.append(CalendarEventOutput(
                event_id=event.get("id"),
                title=event.get("title"),
                start_time=event.get("start_time"),
                end_time=event.get("end_time"),
                is_all_day=event.get("is_all_day", False),
                status=event.get("status"),
            ))

        output = CalendarOutput(
            events=[e.model_dump() for e in event_outputs],
            timezone=result.get("timezone"),
            message=result.get("message"),
        )

        return AgentSkillOutput(
            status="success",
            skill=operation,
            output=output.model_dump(),
            message=result.get("message", f"✅ Found {len(events)} events"),
        ).model_dump()

    async def _handle_update_event(
        self, calendar_tool, operation: str, args: Dict[str, Any], metadata: Optional[Dict]
    ) -> Dict[str, Any]:
        """Handle update_event skill."""
        event_id = args.get("event_id")
        new_start = args.get("new_start")
        new_end = args.get("new_end")
        new_title = args.get("new_title")

        # If event_id is missing, try to extract from context or previous outputs
        if not event_id:
            context = metadata or {}
            event_id = context.get("event_id") or context.get("last_event_id")
            
            # If still missing, try alternative key names
            if not event_id:
                event_id = args.get("id") or args.get("eventId") or args.get("eventID")
            
            if not event_id:
                logger.warning(
                    f"⚠️ [CALENDAR_AGENT] event_id missing for update_event. "
                    f"Args: {args.keys()}, Context: {context.keys() if context else 'None'}"
                )

        if not event_id:
            return self._build_error_response(
                skill_id=operation,
                error_type="MissingParameterError",
                message="Missing required parameter 'event_id' for update_event. Please specify which event to update.",
            )

        result = await calendar_tool.update_event(
            event_id=event_id,
            new_start=new_start,
            new_end=new_end,
            new_title=new_title,
        )

        # Handle error case
        if "error" in result:
            return self._build_error_response(
                skill_id=operation,
                error_type=result["error"].get("type", "UpdateError"),
                message=result["error"].get("message", "Failed to update event"),
                details=result["error"].get("details"),
            )

        # Build standardized output
        event_output = CalendarEventOutput(
            event_id=result.get("event_id"),
            title=result.get("title"),
            start_time=result.get("start_time"),
            end_time=result.get("end_time"),
            timezone=result.get("timezone"),
            resource_link=result.get("link"),
        )

        output = CalendarOutput(
            event=event_output,
            timezone=result.get("timezone"),
            message=result.get("message"),
        )

        return AgentSkillOutput(
            status="success",
            skill=operation,
            output=output.model_dump(),
            message=result.get("message", f"✅ Event updated"),
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


calendar_agent = CalendarAgent()
