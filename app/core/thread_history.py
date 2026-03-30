# app/core/thread_history.py
"""
Thread History Service: Rolling Summary Model

Stores conversation history as exchanges (user + assistant per row).
When exchanges exceed MAX_VERBATIM_EXCHANGES, older ones are summarized
and deleted to keep the database lean.
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from uuid import uuid4

from app.core.supabase_client import supabase_client
from app.core.gemini_client import call_gemini_text

logger = logging.getLogger(__name__)

# Configuration
MAX_VERBATIM_EXCHANGES = 10     # Keep last 10 exchanges verbatim
MAX_CONTENT_LENGTH = 4000       # Max chars per message content


class ThreadHistoryService:
    """Manages conversation history with rolling summarization."""
    
    def __init__(self):
        self.db = supabase_client
    
    async def save_exchange(
        self,
        thread_id: str,
        org_id: str,
        workspace_id: str,
        user_id: Optional[str],
        user_message: str,
        assistant_response: str,
        session_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """
        Save a user-assistant exchange as a single row.
        Triggers summarization if exchanges exceed MAX_VERBATIM_EXCHANGES.
        """
        if not self.db:
            logger.warning("[ThreadHistory] Supabase not initialized")
            return {}
        
        # Get next exchange number
        exchange_number = await self._get_next_exchange_number(thread_id, org_id)
        
        # Truncate content if needed
        user_msg = self._truncate(user_message)
        assistant_msg = self._truncate(assistant_response)
        
        # Insert exchange
        try:
            result = self.db.table("thread_history").insert({
                "id": str(uuid4()),
                "thread_id": thread_id,
                "org_id": org_id,
                "workspace_id": workspace_id,
                "user_id": user_id,
                "user_message": user_msg,
                "assistant_response": assistant_msg,
                "session_id": session_id,
                "exchange_number": exchange_number,
                "metadata": metadata or {},
                "is_archived": False,
            }).execute()
            
            logger.info(f"[ThreadHistory] Saved exchange #{exchange_number} for thread {thread_id[:8]}...")
            
            # Trigger summarization if needed
            await self._summarize_if_needed(thread_id, org_id)
            
            return result.data[0] if result.data else {}
            
        except Exception as e:
            logger.error(f"[ThreadHistory] Failed to save exchange: {e}")
            return {}
    
    async def get_context_for_llm(
        self,
        thread_id: str,
        org_id: str,
        max_exchanges: int = MAX_VERBATIM_EXCHANGES,
    ) -> str:
        """
        Get formatted conversation context for LLM.
        Returns: [Summary of older turns] + [Recent 10 exchanges]
        """
        if not self.db:
            return ""
        
        parts = []
        
        # 1. Get summary (if exists)
        summary = await self._get_thread_summary(thread_id, org_id)
        if summary:
            parts.append(f"## Earlier Conversation Summary\n{summary}\n")
        
        # 2. Get recent exchanges
        exchanges = await self._get_recent_exchanges(thread_id, org_id, max_exchanges)
        
        if exchanges:
            parts.append("## Recent Conversation")
            for ex in exchanges:
                user_msg = (ex.get("user_message") or "")[:500]
                asst_msg = (ex.get("assistant_response") or "")[:500]
                parts.append(f"**User**: {user_msg}")
                parts.append(f"**Assistant**: {asst_msg}")
                
                # Include metadata with actionable details (email, calendar, etc.)
                meta = ex.get("metadata") or {}
                detail_parts = []
                if meta.get("email_subject"):
                    detail_parts.append(f"Subject: {meta['email_subject']}")
                if meta.get("email_to"):
                    detail_parts.append(f"To: {meta['email_to']}")
                if meta.get("email_body"):
                    detail_parts.append(f"Body: {meta['email_body'][:300]}")
                if meta.get("draft_id"):
                    detail_parts.append(f"Draft ID: {meta['draft_id']}")
                if meta.get("event_title"):
                    detail_parts.append(f"Event: {meta['event_title']}")
                if meta.get("intent"):
                    detail_parts.append(f"Intent: {meta['intent']}")
                if detail_parts:
                    parts.append(f"  _Details: {', '.join(detail_parts)}_")
                parts.append("")
        
        context = "\n".join(parts)
        if context:
            logger.info(f"[ThreadHistory] Built context ({len(context)} chars)")
        
        return context
    
    async def _get_recent_exchanges(
        self,
        thread_id: str,
        org_id: str,
        limit: int = MAX_VERBATIM_EXCHANGES,
    ) -> List[Dict]:
        """Get recent exchanges ordered by exchange_number."""
        try:
            result = self.db.table("thread_history") \
                .select("user_message, assistant_response, exchange_number, metadata") \
                .eq("thread_id", thread_id) \
                .eq("org_id", org_id) \
                .eq("is_archived", False) \
                .order("exchange_number", desc=False) \
                .limit(limit) \
                .execute()
            
            return result.data or []
        except Exception as e:
            logger.error(f"[ThreadHistory] Failed to get exchanges: {e}")
            return []
    
    async def _get_thread_summary(self, thread_id: str, org_id: str) -> Optional[str]:
        """Get the summary for older exchanges."""
        try:
            result = self.db.table("thread_summaries") \
                .select("summary") \
                .eq("thread_id", thread_id) \
                .eq("org_id", org_id) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            
            if result.data:
                return result.data[0].get("summary")
            return None
        except Exception as e:
            logger.warning(f"[ThreadHistory] Failed to get summary: {e}")
            return None
    
    async def _get_next_exchange_number(self, thread_id: str, org_id: str) -> int:
        """Get the next exchange number for a thread."""
        try:
            result = self.db.table("thread_history") \
                .select("exchange_number") \
                .eq("thread_id", thread_id) \
                .eq("org_id", org_id) \
                .order("exchange_number", desc=True) \
                .limit(1) \
                .execute()
            
            if result.data and result.data[0].get("exchange_number"):
                return result.data[0]["exchange_number"] + 1
            return 1
        except Exception as e:
            logger.warning(f"[ThreadHistory] Error getting exchange number: {e}")
            return 1
    
    async def _summarize_if_needed(self, thread_id: str, org_id: str) -> bool:
        """
        Check if we need to summarize and delete old exchanges.
        Triggers when total exchanges > MAX_VERBATIM_EXCHANGES.
        """
        try:
            # Count total exchanges
            result = self.db.table("thread_history") \
                .select("id, exchange_number, user_message, assistant_response") \
                .eq("thread_id", thread_id) \
                .eq("org_id", org_id) \
                .eq("is_archived", False) \
                .order("exchange_number", desc=False) \
                .execute()
            
            exchanges = result.data or []
            total = len(exchanges)
            
            if total <= MAX_VERBATIM_EXCHANGES:
                return False
            
            # We have too many - summarize the oldest ones
            to_summarize_count = total - MAX_VERBATIM_EXCHANGES
            exchanges_to_summarize = exchanges[:to_summarize_count]
            
            logger.info(f"[ThreadHistory] Summarizing {to_summarize_count} old exchanges...")
            
            # Build content for summarization
            conversation_text = self._format_exchanges_for_summary(exchanges_to_summarize)
            
            # Generate summary via LLM
            summary = await self._generate_summary(conversation_text)
            
            if not summary:
                logger.warning("[ThreadHistory] Summary generation failed")
                return False
            
            # Get existing summary (if any) to append
            existing_summary = await self._get_thread_summary(thread_id, org_id)
            if existing_summary:
                summary = f"{existing_summary}\n\n{summary}"
            
            # Save/update summary
            await self._save_summary(
                thread_id=thread_id,
                org_id=org_id,
                summary=summary,
                turns_summarized=to_summarize_count,
                last_exchange_number=exchanges_to_summarize[-1]["exchange_number"],
            )
            
            # Delete the summarized exchanges
            exchange_ids = [ex["id"] for ex in exchanges_to_summarize]
            self.db.table("thread_history") \
                .delete() \
                .in_("id", exchange_ids) \
                .execute()
            
            logger.info(f"[ThreadHistory] Summarized and deleted {to_summarize_count} exchanges")
            return True
            
        except Exception as e:
            logger.error(f"[ThreadHistory] Summarization failed: {e}")
            return False
    
    def _format_exchanges_for_summary(self, exchanges: List[Dict]) -> str:
        """Format exchanges for LLM summarization."""
        lines = []
        for ex in exchanges:
            user_msg = ex.get("user_message", "")[:500]
            asst_msg = ex.get("assistant_response", "")[:500]
            lines.append(f"User: {user_msg}")
            lines.append(f"Assistant: {asst_msg}")
        return "\n".join(lines)
    
    async def _generate_summary(self, conversation_text: str) -> Optional[str]:
        """Generate a summary of the conversation using Gemini."""
        try:
            prompt = f"""Summarize this conversation history in 2-3 sentences.
Focus on: what the user asked about, what information was provided, and any important outcomes.
Be concise but capture the key topics discussed.

Conversation:
{conversation_text}

Summary:"""
            
            summary = await call_gemini_text(prompt)
            return summary.strip() if summary else None
        except Exception as e:
            logger.error(f"[ThreadHistory] LLM summary failed: {e}")
            return None
    
    async def _save_summary(
        self,
        thread_id: str,
        org_id: str,
        summary: str,
        turns_summarized: int,
        last_exchange_number: int,
    ) -> bool:
        """Save or update the thread summary."""
        try:
            # Check if summary exists
            existing = self.db.table("thread_summaries") \
                .select("id") \
                .eq("thread_id", thread_id) \
                .eq("org_id", org_id) \
                .execute()
            
            if existing.data:
                # Update existing
                self.db.table("thread_summaries") \
                    .update({
                        "summary": summary,
                        "turns_summarized": turns_summarized,
                        "last_exchange_number": last_exchange_number,
                        "updated_at": datetime.utcnow().isoformat(),
                    }) \
                    .eq("id", existing.data[0]["id"]) \
                    .execute()
            else:
                # Insert new
                self.db.table("thread_summaries").insert({
                    "id": str(uuid4()),
                    "thread_id": thread_id,
                    "org_id": org_id,
                    "summary": summary,
                    "turns_summarized": turns_summarized,
                    "last_exchange_number": last_exchange_number,
                }).execute()
            
            return True
        except Exception as e:
            logger.error(f"[ThreadHistory] Failed to save summary: {e}")
            return False
    
    def _truncate(self, text: str) -> str:
        """Truncate text to MAX_CONTENT_LENGTH."""
        if len(text) <= MAX_CONTENT_LENGTH:
            return text
        return text[:MAX_CONTENT_LENGTH - 3] + "..."


# Singleton instance
_thread_history_service: Optional[ThreadHistoryService] = None


def get_thread_history_service() -> ThreadHistoryService:
    """Get or create the ThreadHistoryService singleton."""
    global _thread_history_service
    if _thread_history_service is None:
        _thread_history_service = ThreadHistoryService()
    return _thread_history_service
