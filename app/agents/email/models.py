from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# INPUT/PLANNING SCHEMAS (Existing)
# ═══════════════════════════════════════════════════════════════

class EmailContent(BaseModel):
    """Input schema for email content (used in planning)."""
    to: Optional[str] = Field(default=None, description="Recipient email address.")
    subject: Optional[str] = Field(default=None, description="Subject of the email.")
    body: Optional[str] = Field(default=None, description="Body content of the email.")
    thread_reference: Optional[str] = Field(default=None, description="The ID of the thread to reply to, if applicable.")


class EmailPlan(BaseModel):
    """Planning output for email agent graph."""
    thought_process: str = Field(description="Step-by-step reasoning for the chosen action.")
    action: str = Field(default="none", description="The action to take: 'send', 'draft', 'reply', 'fetch_thread', 'list_threads', 'none'.")
    
    # Payload
    email: Optional[EmailContent] = Field(default=None, description="Details of the email to send or draft.")
    
    # Read-Only Payloads (For List/Fetch)
    thread_context: Optional[str] = Field(default=None, description="Summary or content of fetched threads.")


# ═══════════════════════════════════════════════════════════════
# OUTPUT SCHEMAS (For Phase 4 standardized responses)
# ═══════════════════════════════════════════════════════════════

class EmailItem(BaseModel):
    """Single email item in a list."""
    id: str
    from_address: Optional[str] = Field(default=None, alias="from")
    subject: Optional[str] = None
    date: Optional[str] = None
    snippet: Optional[str] = None
    
    class Config:
        populate_by_name = True  # Allow both 'from' and 'from_address'


class EmailOutput(BaseModel):
    """
    Standardized output for email operations.
    
    Used by:
    - send_email: Returns message_id, to, subject, body
    - create_draft: Returns draft_id, to, subject, body
    - list_threads: Returns emails list
    - get_thread: Returns thread content
    """
    # Identifiers
    message_id: Optional[str] = None
    draft_id: Optional[str] = None
    thread_id: Optional[str] = None
    
    # Email content (for send/draft operations)
    to: Optional[List[str]] = None
    cc: Optional[List[str]] = None
    bcc: Optional[List[str]] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    
    # For list operations
    emails: Optional[List[EmailItem]] = None
    account: Optional[str] = None  # Connected email account
    
    # Resource link for UI
    resource_link: Optional[str] = None
    
    # Human-readable message
    message: Optional[str] = None


class EmailErrorOutput(BaseModel):
    """Error output for email operations."""
    type: str = "EmailError"
    message: str
    operation: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    retryable: bool = False

