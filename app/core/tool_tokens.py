"""
Dynamic token configuration with MongoDB integration.
Fetches user tokens from MongoDB toolaccounts collection.
"""

import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from dotenv import load_dotenv

# Load environment variables from .env file in project root
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)

# MongoDB Connection
MONGODB_URI = os.environ.get(
    "MONGODB_URI", "mongodb://localhost:27017/"  # Default fallback
)
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "genios")
MONGODB_COLLECTION = "toolaccounts"

# MongoDB configuration (silent on startup)

# Initialize MongoDB client (lazy connection)
_mongo_client = None
_mongo_db = None


def get_mongodb_client():
    """Get or create MongoDB client instance."""
    global _mongo_client, _mongo_db

    if _mongo_client is None:
        try:
            _mongo_client = MongoClient(
                MONGODB_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000
            )
            # Test connection
            _mongo_client.admin.command("ping")
            _mongo_db = _mongo_client[MONGODB_DATABASE]
        except (ConnectionFailure, OperationFailure) as e:
            logger.error(f"❌ MongoDB connection failed: {str(e)}")
            _mongo_client = None
            _mongo_db = None

    return _mongo_db


# Static Gmail Token (FALLBACK for testing only)
STATIC_GMAIL_TOKEN = {
    "access_token": "ya29.a0AfH6SMB_example_token_replace_with_real_token",
    "refresh_token": "1//0g_example_refresh_token_replace_with_real_token",
    "client_id": "your-client-id.apps.googleusercontent.com",
    "client_secret": "your-client-secret",
    "token_uri": "https://oauth2.googleapis.com/token",
    "scope": ["https://www.googleapis.com/auth/gmail.modify"],
    "expires_at": 9999999999000,
}

# Static Calendar Token (FALLBACK for testing only)
STATIC_CALENDAR_TOKEN = {
    "access_token": "ya29.a0AfH6SMB_example_calendar_token",
    "refresh_token": "1//0g_example_calendar_refresh_token",
    "client_id": "your-client-id.apps.googleusercontent.com",
    "client_secret": "your-client-secret",
    "token_uri": "https://oauth2.googleapis.com/token",
    "scope": ["https://www.googleapis.com/auth/calendar"],
    "expires_at": 9999999999000,
}


def get_tool_token(tool_name: str, user_id: str = None) -> Optional[Dict[str, Any]]:
    """
    Get token for a tool from MongoDB.

    Args:
        tool_name: Name of the tool (e.g., 'send_email', 'list_threads')
        user_id: User ID for fetching user-specific tokens

    Returns:
        Token dictionary with access_token, refresh_token, client_id, etc.
    """
    # Map tool names to tool types
    email_tools = ["send_email", "list_threads", "get_thread", "create_draft", "gmail"]
    calendar_tools = ["create_event", "list_events", "update_event", "calendar"]

    # Determine which tool to query
    tool_type = None
    if tool_name in email_tools:
        tool_type = "gmail"
    elif tool_name in calendar_tools:
        tool_type = "calendar"
    else:
        logger.warning(f"Unknown tool name: {tool_name}")
        return None

    # If no user_id provided, fall back to static tokens
    if not user_id:
        logger.error(f"❌ [TOKEN] No user_id provided for {tool_name}")
        return STATIC_GMAIL_TOKEN if tool_type == "gmail" else STATIC_CALENDAR_TOKEN

    # Try to fetch from MongoDB
    try:
        db = get_mongodb_client()
        if db is None:
            logger.error("❌ [TOKEN] MongoDB not available")
            return STATIC_GMAIL_TOKEN if tool_type == "gmail" else STATIC_CALENDAR_TOKEN

        collection = db[MONGODB_COLLECTION]

        # Query for user's tool token
        query = {"userId": user_id, "tool": tool_type}

        logger.info(f"🔍 [TOKEN] Query: userId={user_id}, tool={tool_type}")
        token_doc = collection.find_one(query)

        if token_doc:
            user_email = token_doc.get("metadata", {}).get("email", "N/A")
            logger.info(
                f"✅ [TOKEN] Found for user={user_id}, tool={tool_type}, email={user_email}"
            )

            # Extract and return token fields
            token = {
                "access_token": token_doc.get("access_token"),
                "refresh_token": token_doc.get("refresh_token"),
                "client_id": token_doc.get("client_id"),
                "client_secret": token_doc.get("client_secret"),
                "token_uri": token_doc.get(
                    "token_uri", "https://oauth2.googleapis.com/token"
                ),
                "scope": token_doc.get("scope", []),
                "expires_at": token_doc.get("expires_at"),
            }

            return token
        else:
            logger.error(f"❌ [TOKEN] NOT FOUND for user={user_id}, tool={tool_type}")
            return STATIC_GMAIL_TOKEN if tool_type == "gmail" else STATIC_CALENDAR_TOKEN

    except Exception as e:
        logger.error(f"❌ Error fetching token from MongoDB: {str(e)}")
        logger.info("Falling back to static tokens")
        return STATIC_GMAIL_TOKEN if tool_type == "gmail" else STATIC_CALENDAR_TOKEN
