import os
import logging
import datetime
import re
from typing import List, Dict, Any
from zoneinfo import ZoneInfo  # <--- NEW IMPORT
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# Timezone aliases for Windows compatibility
# Some timezone names from Google Calendar need to be mapped to IANA names
TIMEZONE_ALIASES = {
    "Asia/Kolkata": "Asia/Calcutta",
    "Asia/Calcutta": "Asia/Calcutta",  # Keep as-is
}

def _normalize_timezone(tz_name: str) -> str:
    """Normalize timezone name for ZoneInfo compatibility."""
    return TIMEZONE_ALIASES.get(tz_name, tz_name)


def _get_safe_zoneinfo(tz_name: str) -> ZoneInfo:
    """Safe wrapper for ZoneInfo creation with UTC fallback."""
    normalized = _normalize_timezone(tz_name)
    try:
        return ZoneInfo(normalized)
    except Exception as e:
        logger.warning(f"Failed to load timezone '{normalized}': {e}. Falling back to UTC.")
        return ZoneInfo("UTC")


def _build_error_response(error: Exception, operation: str) -> Dict[str, Any]:
    """Build a structured error response from an exception."""
    error_str = str(error)
    
    # Try to extract structured error info from Google API errors
    error_type = type(error).__name__
    error_details = {}
    
    # Check for OAuth/API errors with structured info
    if hasattr(error, 'error_details'):
        error_details = error.error_details
    elif "invalid_client" in error_str:
        error_type = "AuthenticationError"
        error_details = {"code": "invalid_client", "description": "OAuth client configuration issue"}
    elif "invalid_grant" in error_str:
        error_type = "AuthenticationError"
        error_details = {"code": "invalid_grant", "description": "Token expired or revoked"}
    elif "403" in error_str or "forbidden" in error_str.lower():
        error_type = "PermissionError"
        error_details = {"code": "forbidden", "description": "Insufficient permissions"}
    elif "404" in error_str or "not found" in error_str.lower():
        error_type = "NotFoundError"
        error_details = {"code": "not_found", "description": "Resource not found"}
    elif "rate limit" in error_str.lower() or "429" in error_str:
        error_type = "RateLimitError"
        error_details = {"code": "rate_limit", "description": "API rate limit exceeded"}
    
    return {
        "error": {
            "type": error_type,
            "message": f"Error {operation}: {error_str}",
            "operation": operation,
            "details": error_details,
        }
    }


def _parse_time_to_iso(time_str: str, reference_date: datetime.datetime) -> str:
    """
    Convert natural language time (e.g., "11 pm", "2:30 pm") to ISO 8601 format.

    Args:
        time_str: Time string like "11 pm", "2:30 pm", "14:00"
        reference_date: Date to use (e.g., today's date)

    Returns:
        ISO 8601 datetime string or original if can't parse
    """
    if not time_str or isinstance(time_str, datetime.datetime):
        return time_str

    time_str = str(time_str).strip().lower()

    # Already ISO 8601?
    if "T" in time_str or "t" in time_str:
        return time_str

    try:
        # Pattern: "11 pm", "11pm", "2:30 pm", "14:00"
        # Extract hour, minute (optional), am/pm (optional)
        match = re.match(r"(\d{1,2}):?(\d{2})?\s*(am|pm)?", time_str)
        if not match:
            logger.warning(f"Could not parse time: {time_str}")
            return time_str

        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3)

        # Convert 12-hour to 24-hour format if needed
        if meridiem:
            if meridiem == "pm" and hour != 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0

        # Create datetime with the reference date
        iso_dt = reference_date.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return iso_dt.isoformat()
    except Exception as e:
        logger.warning(f"Error parsing time '{time_str}': {e}")
        return time_str


class CalendarTool:
    def __init__(self, tokens):
        """
        Initialize Google Calendar API using MongoDB tokens.
        """
        self.creds = Credentials(
            token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_uri=tokens.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=tokens.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=tokens.get("client_secret")
            or os.environ.get("GOOGLE_CLIENT_SECRET"),
            scopes=tokens.get("scope", ["https://www.googleapis.com/auth/calendar"]),
        )
        self.service = build("calendar", "v3", credentials=self.creds)

    async def get_user_timezone(self) -> str:
        """
        Fetches the primary timeZone settings from the calendar.
        """
        try:
            setting = self.service.settings().get(setting="timezone").execute()
            return setting.get("value", "UTC")
        except:
            return "UTC"

    async def list_events(self, date_range: str = "today") -> Dict[str, Any]:
        """
        Lists events for the FULL day or WEEK in the USER'S Timezone.
        Returns a structured dict with timezone info and list of events.
        """
        try:
            # 1. Get User Timezone
            user_tz_name = await self.get_user_timezone()
            if not user_tz_name:
                user_tz_name = "UTC"

            # 2. Get 'Now' and Start of Day
            # Use safe wrapper for Windows compatibility
            user_tz = _get_safe_zoneinfo(user_tz_name)
            
            now_local = datetime.datetime.now(user_tz)
            start_of_day_local = now_local.replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            # 3. Determine End Time (Always fetch 7 days context if we can)
            #    We rely on the LLM to filter what the user actually asked for.
            if "today" in str(date_range).lower():
                # If specifically 'today', we can just fetch 1 day,
                # BUT fetching a week is safer for context as discussed.
                end_of_time_local = start_of_day_local + datetime.timedelta(days=7)
            else:
                end_of_time_local = start_of_day_local + datetime.timedelta(days=7)

            # 4. Convert to UTC for Google API
            time_min = (
                start_of_day_local.astimezone(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            time_max = (
                end_of_time_local.astimezone(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )

            print(f"   🛠️ Tool: Fetching {time_min} to {time_max} ({user_tz_name})")

            events_result = (
                self.service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=50,  # Increased limit for weekly view
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )

            raw_events = events_result.get("items", [])

            if not raw_events:
                return {
                    "timezone": user_tz_name,
                    "events": [],
                    "message": f"No events found from {start_of_day_local.date()} onwards."
                }

            events_list = []
            for event in raw_events:
                status = event.get("status", "confirmed")
                summary = event.get("summary", "No Title")
                e_id = event.get("id")

                start_raw = event["start"].get("dateTime", event["start"].get("date"))
                is_all_day = "T" not in start_raw

                # Format the start time
                try:
                    if not is_all_day:
                        dt_obj = datetime.datetime.fromisoformat(
                            start_raw.replace("Z", "+00:00")
                        )
                        local_dt = dt_obj.astimezone(user_tz)
                        # Include DATE and TIME (e.g., "2025-12-22 04:00 PM")
                        start_fmt = local_dt.strftime("%Y-%m-%d %I:%M %p")
                    else:
                        start_fmt = start_raw  # "2025-12-22" (All Day)
                except:
                    start_fmt = start_raw

                events_list.append({
                    "id": e_id,
                    "title": summary,
                    "status": status,
                    "start_time": start_fmt,
                    "is_all_day": is_all_day
                })

            return {
                "timezone": user_tz_name,
                "events": events_list,
                "message": f"Found {len(events_list)} events"
            }

        except Exception as e:
            return _build_error_response(e, "listing events")

    async def create_event(
        self,
        title: str,
        start_time: str,
        end_time: str = None,
        attendees: List[str] = [],
    ) -> Dict[str, Any]:
        """
        Creates a new event using the User's Real Timezone.
        Returns a structured dict with event details.
        """
        try:
            user_tz = await self.get_user_timezone()

            # Validate start_time
            if not start_time:
                return {"error": "start_time is required"}

            clean_start = start_time.replace("Z", "") if start_time else ""

            clean_end = ""
            if not end_time:
                try:
                    dt_start = datetime.datetime.fromisoformat(clean_start)
                    dt_end = dt_start + datetime.timedelta(minutes=30)
                    clean_end = dt_end.isoformat()
                except Exception as e:
                    clean_end = clean_start
            else:
                clean_end = end_time.replace("Z", "") if end_time else clean_start

            event = {
                "summary": title,
                "start": {
                    "dateTime": clean_start,
                    "timeZone": user_tz,
                },
                "end": {"dateTime": clean_end, "timeZone": user_tz},
            }

            if attendees:
                event["attendees"] = [{"email": email} for email in attendees]

            created_event = (
                self.service.events().insert(calendarId="primary", body=event).execute()
            )
            
            return {
                "event_id": created_event.get("id"),
                "title": title,
                "start_time": clean_start,
                "end_time": clean_end,
                "timezone": user_tz,
                "attendees": attendees,
                "link": created_event.get("htmlLink"),
                "message": f"Event Created! Link: {created_event.get('htmlLink')}"
            }

        except Exception as e:
            logger.error(f"❌ Error creating event: {str(e)}", exc_info=True)
            return _build_error_response(e, "creating event")

    async def get_free_busy(self, time_min: str, time_max: str) -> str:
        """
        Checks availability for 'Suggest Slots' skill.
        """
        try:
            body = {
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": "primary"}],
            }
            events = self.service.freebusy().query(body=body).execute()
            busy_slots = events["calendars"]["primary"]["busy"]

            return f"Busy Slots found: {busy_slots}"
        except Exception as e:
            return f"Error checking free/busy: {str(e)}"

    async def update_event(
        self,
        event_id: str,
        new_start: str = None,
        new_end: str = None,
        new_title: str = None,
    ) -> Dict[str, Any]:
        """
        Updates an existing event using the User's Real Timezone.
        Returns a structured dict with updated event details.
        """
        try:
            # Get user timezone (same approach as create_event)
            user_tz = await self.get_user_timezone()

            # Get current event
            event = (
                self.service.events()
                .get(calendarId="primary", eventId=event_id)
                .execute()
            )

            if new_title:
                logger.info(f"📝 Updating Title: {new_title}")
                event["summary"] = new_title

            if new_start:
                # Check if already in ISO format (same logic as create_event)
                # If already ISO format, just remove "Z" like create_event does
                if "T" in new_start or "t" in new_start:
                    # Already ISO format - handle same as create_event
                    clean_start = new_start.replace("Z", "") if new_start else ""
                else:
                    # Natural language time (e.g., "11 pm", "2:30 pm") - need parsing
                    user_tz_obj = _get_safe_zoneinfo(user_tz)
                    reference_date = datetime.datetime.now(user_tz_obj)
                    iso_start = _parse_time_to_iso(new_start, reference_date)
                    clean_start = iso_start.replace("Z", "")
                    logger.info(f"🕐 Parsed natural language: {new_start} → {clean_start}")

                logger.info(f"🕐 Updating Start: {clean_start} (timezone: {user_tz})")
                event["start"]["dateTime"] = clean_start
                event["start"]["timeZone"] = user_tz  # Use timezone string directly (same as create_event)

            if new_end:
                # Check if already in ISO format (same logic as create_event)
                # If already ISO format, just remove "Z" like create_event does
                if "T" in new_end or "t" in new_end:
                    # Already ISO format - handle same as create_event
                    clean_end = new_end.replace("Z", "") if new_end else ""
                else:
                    # Natural language time (e.g., "11 pm", "2:30 pm") - need parsing
                    user_tz_obj = _get_safe_zoneinfo(user_tz)
                    reference_date = datetime.datetime.now(user_tz_obj)
                    iso_end = _parse_time_to_iso(new_end, reference_date)
                    clean_end = iso_end.replace("Z", "")
                    logger.info(f"🕐 Parsed natural language: {new_end} → {clean_end}")

                logger.info(f"🕐 Updating End: {clean_end} (timezone: {user_tz})")
                event["end"]["dateTime"] = clean_end
                event["end"]["timeZone"] = user_tz  # Use timezone string directly (same as create_event)

            # Call Google API
            logger.info(f"📤 Sending to Google API with timezone: {user_tz}")
            updated_event = (
                self.service.events()
                .update(calendarId="primary", eventId=event_id, body=event)
                .execute()
            )

            logger.info(f"✅ Google API Response: {updated_event.get('htmlLink')}")
            
            return {
                "event_id": event_id,
                "title": updated_event.get("summary"),
                "start_time": updated_event.get("start", {}).get("dateTime"),
                "end_time": updated_event.get("end", {}).get("dateTime"),
                "timezone": user_tz,
                "link": updated_event.get("htmlLink"),
                "message": f"Event Updated! Link: {updated_event.get('htmlLink')}"
            }
        except Exception as e:
            logger.error(f"❌ Error updating event {event_id}: {str(e)}", exc_info=True)
            return _build_error_response(e, "updating event")

    async def delete_event(self, event_id: str) -> str:
        """
        Deletes an event by ID.
        """
        try:
            self.service.events().delete(
                calendarId="primary", eventId=event_id
            ).execute()
            return "✅ Event cancelled/deleted successfully."
        except Exception as e:
            return _build_error_response(e, "deleting event")
