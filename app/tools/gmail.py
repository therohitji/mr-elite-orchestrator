import os
from typing import Dict, Any
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.message import EmailMessage
import base64


def _build_error_response(error: Exception, operation: str) -> Dict[str, Any]:
    """Build a structured error response from an exception."""
    error_str = str(error)
    
    # Try to extract structured error info from Google API errors
    error_type = type(error).__name__
    error_details = {}
    
    # Check for OAuth/API errors with structured info
    if "invalid_client" in error_str:
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

class GmailTool:
    def __init__(self, tokens):
        """
        tokens: The MongoDB document containing access_token, client_id, etc.
        """
        # 1. Handle Timestamp Format (DB is milliseconds, Google wants seconds)
        # If 'expiry' is in the far future (trillions), divide by 1000
        expiry = tokens.get("expires_at")
        if expiry and expiry > 100000000000:
            # It's likely in milliseconds, convert to expected format if needed
            # or just let the library handle 'expiry' via the token_expiry kwarg if you were reconstructing differently.
            # Usually, passing the expiry directly as is causes the library to think it's valid forever.
            # To force a refresh check, we can sometimes omit it or let the refresh logic handle 401s.
            pass

        # 2. Extract Credentials safely
        self.creds = Credentials(
            token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_uri=tokens.get("token_uri", "https://oauth2.googleapis.com/token"),
            # --- THE CRITICAL FIX ---
            # Read Client ID/Secret from the DB tokens dictionary FIRST
            client_id=tokens.get("client_id") or os.environ.get("GOOGLE_CLIENT_ID"),
            client_secret=tokens.get("client_secret")
            or os.environ.get("GOOGLE_CLIENT_SECRET"),
            # Handle 'scope' (DB uses singular 'scope' list, Google wants plural 'scopes')
            scopes=tokens.get(
                "scope", ["https://www.googleapis.com/auth/gmail.modify"]
            ),
        )

        self.service = build("gmail", "v1", credentials=self.creds)

    async def get_thread(self, thread_id: str) -> str:
        print(f"🔍 GmailTool: Fetching thread ID '{thread_id}'")
        try:
            thread = (
                self.service.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
            messages = thread.get("messages", [])

            context = f"--- Thread ID: {thread_id} ---\n"
            for msg in messages:
                snippet = msg.get("snippet", "")
                payload = msg.get("payload", {})
                headers = payload.get("headers", [])

                sender = next(
                    (h["value"] for h in headers if h["name"] == "From"), "Unknown"
                )
                date = next(
                    (h["value"] for h in headers if h["name"] == "Date"), "Unknown"
                )

                context += f"From: {sender} | Date: {date}\nBody Snippet: {snippet}\n\n"

            return context
        except Exception as e:
            # If the token is invalid, this is where it usually fails
            return _build_error_response(e, "retrieving thread")

    async def list_threads(self, limit=5) -> dict:
        """
        Lists recent email threads.
        Returns a structured dict with email list.
        """
        print(f"🔍 GmailTool: Listing recent threads")
        try:
            # 1. Get account info
            profile = self.service.users().getProfile(userId="me").execute()
            connected_email = profile.get("emailAddress")
            print(f"🕵️ Gmail Debug: Connected as '{connected_email}'")
            print(f"   (Total Messages in account: {profile.get('messagesTotal')})")

            # 2. List Threads
            results = (
                self.service.users()
                .threads()
                .list(userId="me", maxResults=limit, includeSpamTrash=True)
                .execute()
            )

            threads = results.get("threads", [])

            if not threads:
                return {
                    "account": connected_email,
                    "emails": [],
                    "message": f"No emails found in account {connected_email}"
                }

            # 3. Build structured email list
            emails_list = []
            for t in threads:
                # Get thread details for more info
                thread_detail = (
                    self.service.users()
                    .threads()
                    .get(userId="me", id=t["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"])
                    .execute()
                )
                
                messages = thread_detail.get("messages", [])
                first_msg = messages[0] if messages else {}
                headers = first_msg.get("payload", {}).get("headers", [])
                
                # Extract header values
                from_addr = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
                subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
                date = next((h["value"] for h in headers if h["name"] == "Date"), "Unknown")
                
                emails_list.append({
                    "id": t["id"],
                    "from": from_addr,
                    "subject": subject,
                    "date": date,
                    "snippet": t.get("snippet", "No preview"),
                })

            return {
                "account": connected_email,
                "emails": emails_list,
                "message": f"Found {len(emails_list)} emails"
            }

        except Exception as e:
            return _build_error_response(e, "listing threads")

    async def create_draft(
        self, to, subject: str, body: str = None, thread_id: str = None
    ) -> dict:
        try:
            message = EmailMessage()
            # Handle None or empty body - use empty string as default
            if body is None or body == "":
                body = ""
            message.set_content(body)
            
            # Handle 'to' as list or string - convert list to comma-separated string
            if isinstance(to, list):
                to_str = ", ".join(to)
            else:
                to_str = to
            
            message["To"] = to_str
            message["Subject"] = subject

            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            create_message = {"message": {"raw": encoded_message}}

            if thread_id:
                create_message["message"]["threadId"] = thread_id

            draft = (
                self.service.users()
                .drafts()
                .create(userId="me", body=create_message)
                .execute()
            )
            # Return full draft details for UI display
            return {
                "draft_id": draft['id'],
                "to": to,
                "subject": subject,
                "body": body,
                "thread_id": thread_id,
                "message": f"Draft created! ID: {draft['id']}"
            }
        except Exception as e:
            return _build_error_response(e, "creating draft")

    async def send_email(
        self, to, subject: str, body: str = None, thread_id: str = None
    ) -> dict:
        try:
            message = EmailMessage()
            # Handle None or empty body - use empty string as default
            if body is None or body == "":
                body = ""
            message.set_content(body)
            
            # Handle 'to' as list or string - convert list to comma-separated string
            if isinstance(to, list):
                to_str = ", ".join(to)
            else:
                to_str = to
            
            message["To"] = to_str
            message["Subject"] = subject

            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            create_message = {"raw": encoded_message}

            if thread_id:
                create_message["threadId"] = thread_id

            sent_message = (
                self.service.users()
                .messages()
                .send(userId="me", body=create_message)
                .execute()
            )
            return {
                "message_id": sent_message['id'],
                "to": to,
                "subject": subject,
                "body": body,
                "thread_id": thread_id,
                "message": f"Email sent! ID: {sent_message['id']}"
            }
        except Exception as e:
            return _build_error_response(e, "sending email")
