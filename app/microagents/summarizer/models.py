"""
Summarizer Agent Models

Input/output schemas for text and email summarization operations.
"""

from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# OUTPUT SCHEMAS (For Phase 4 standardized responses)
# ═══════════════════════════════════════════════════════════════

class SummarizerOutput(BaseModel):
    """
    Standardized output for summarization operations.
    
    Used by:
    - summarize_text: Summarizes provided text content
    - summarize_emails: Summarizes email threads or lists
    """
    # The generated summary
    summary: str
    
    # Source information
    source_type: Literal["text", "emails", "thread", "unknown"] = "unknown"
    source_count: Optional[int] = None  # Number of items summarized (e.g., email count)
    
    # Metadata
    word_count: Optional[int] = None  # Original content word count
    summary_word_count: Optional[int] = None  # Summary word count
    
    # Human-readable message
    message: Optional[str] = None


class SummarizerErrorOutput(BaseModel):
    """Error output for summarization operations."""
    type: str = "SummarizerError"
    message: str
    operation: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    retryable: bool = False
