"""
Tool Factory: Centralized initialization and management of tools.
Handles tool instantiation with tokens and credentials.
"""

from typing import Dict, Any, Optional
from app.tools.gmail import GmailTool
from app.tools.calendar import CalendarTool
from app.core.tool_tokens import get_tool_token
import logging

logger = logging.getLogger(__name__)


class ToolFactory:
    """
    Factory for creating and managing tool instances.
    Ensures consistent token injection and error handling.

    NOTE: Creates fresh tool instances on every call (no caching).
    This ensures token updates in MongoDB are immediately reflected
    without requiring server restart.
    """

    _instances: Dict[str, Any] = {}  # Kept for backwards compatibility but not used

    @staticmethod
    async def get_tool(tool_name: str, user_id: str = None) -> Optional[Any]:
        """
        Get or create a tool instance.

        IMPORTANT: Creates fresh tool every time (no caching) to ensure
        token changes in MongoDB are immediately reflected.

        Args:
            tool_name: Name of the tool (e.g., 'send_email', 'list_threads')
            user_id: User ID for token retrieval

        Returns:
            Tool instance (e.g., GmailTool) or None if not found
        """
        # Determine tool type
        email_tools = ["send_email", "list_threads", "get_thread", "create_draft"]
        calendar_tools = ["create_event", "list_events", "update_event"]

        try:
            if tool_name in email_tools:
                # Fetch FRESH token from MongoDB every time
                token = get_tool_token(tool_name, user_id)
                if not token:
                    logger.error(f"❌ No token found for tool: {tool_name}")
                    return None

                # Create NEW tool instance (don't cache)
                logger.info(f"✅ Creating fresh GmailTool for user: {user_id}")
                tool = GmailTool(token)
                return tool

            elif tool_name in calendar_tools:
                # Fetch FRESH token from MongoDB every time
                token = get_tool_token(tool_name, user_id)
                if not token:
                    logger.error(f"❌ No token found for calendar tool: {tool_name}")
                    return None

                # Create NEW tool instance (don't cache)
                logger.info(f"✅ Creating fresh CalendarTool for user: {user_id}")
                tool = CalendarTool(token)
                return tool
            else:
                logger.warning(f"⚠️ Unknown tool: {tool_name}")
                return None

        except Exception as e:
            logger.error(f"❌ Error creating tool {tool_name}: {str(e)}")
            return None

    @staticmethod
    def clear_cache():
        """Clear cached tool instances (no longer used - tools are created fresh)."""
        # Cache is no longer used since we create fresh tools on every request
        logger.info("⚠️ clear_cache() called but caching is disabled for tool freshness")

    @staticmethod
    def get_cached_tool(tool_name: str, user_id: str = None) -> Optional[Any]:
        """Get tool from cache (deprecated - tools are now created fresh every time)."""
        logger.warning(
            f"⚠️ get_cached_tool() called but caching is disabled. "
            f"Use get_tool() instead to get a fresh tool instance."
        )
        return None
