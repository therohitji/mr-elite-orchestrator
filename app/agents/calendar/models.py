from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# INPUT/PLANNING SCHEMAS (Existing)
# ═══════════════════════════════════════════════════════════════

class CalendarEvent(BaseModel):
    """Input schema for calendar event (used in planning)."""
    id: Optional[str] = Field(default=None, description="The ID of the event, if known (e.g. for updates/deletes).")
    title: Optional[str] = Field(default=None, description="The title of the event.")
    start: Optional[str] = Field(default=None, description="ISO 8601 start time.")
    end: Optional[str] = Field(default=None, description="ISO 8601 end time.")
    attendees: List[str] = Field(default=[], description="List of attendee email addresses.")
    status: Optional[str] = Field(default=None, description="Event status (confirmed, tentative, etc.)")


class CalendarSlot(BaseModel):
    """Free time slot for scheduling suggestions."""
    start: str = Field(description="ISO 8601 start time.")
    end: str = Field(description="ISO 8601 end time.")


class CalendarPlan(BaseModel):
    """Planning output for calendar agent graph."""
    thought_process: str = Field(description="Step-by-step reasoning for the chosen action.")
    action: str = Field(default="none", description="The action to take: 'create', 'update', 'delete', 'list', 'suggest', 'none'.")
    
    # Payload for Actions
    event: Optional[CalendarEvent] = Field(default=None, description="Details of the event to create/update/delete.")
    
    # Read-Only Payloads
    agenda_summary: List[CalendarEvent] = Field(default=[], description="List of events found for an agenda request.")
    suggested_slots: List[CalendarSlot] = Field(default=[], description="List of free slots found.")


# ═══════════════════════════════════════════════════════════════
# OUTPUT SCHEMAS (For Phase 4 standardized responses)
# ═══════════════════════════════════════════════════════════════

class CalendarEventOutput(BaseModel):
    """
    Standardized output for a single calendar event.
    
    Used in both single-event operations (create, update) and list operations.
    """
    event_id: Optional[str] = None
    title: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    timezone: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[List[str]] = None
    is_all_day: bool = False
    status: Optional[str] = None  # confirmed, tentative, cancelled
    resource_link: Optional[str] = None  # Google Calendar link


class CalendarOutput(BaseModel):
    """
    Standardized output for calendar operations.
    
    Used by:
    - create_event: Returns event with event_id, title, times, link
    - update_event: Returns updated event details
    - list_events: Returns events list with timezone
    - delete_event: Returns success message
    """
    # Single event result (for create/update)
    event: Optional[CalendarEventOutput] = None
    
    # Multiple events result (for list)
    events: Optional[List[CalendarEventOutput]] = None
    
    # Timezone context
    timezone: Optional[str] = None
    
    # Human-readable message
    message: Optional[str] = None


class CalendarErrorOutput(BaseModel):
    """Error output for calendar operations."""
    type: str = "CalendarError"
    message: str
    operation: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    retryable: bool = False

