"""
Phase 4: Retry Engine - Exponential backoff retry logic
Handles failed executions with automatic retry and dead letter queue
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID
from datetime import datetime, timedelta
import asyncio

from app.core.supabase_client import supabase_client

logger = logging.getLogger(__name__)


class RetryConfig:
    """Configuration for retry behavior"""

    MAX_RETRIES = 3  # Maximum retry attempts
    INITIAL_BACKOFF_SECONDS = 60  # 1 minute base
    MAX_BACKOFF_SECONDS = 3600  # 1 hour max
    BACKOFF_MULTIPLIER = 2  # Exponential: 2^retry_count


class RetryEngine:
    """
    Manages retry logic for failed atom executions.
    Implements exponential backoff and dead letter queue.
    """

    def __init__(self, org_id: str, workspace_id: str):
        """
        Initialize retry engine.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
        """
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.supabase = supabase_client
        self.config = RetryConfig()

    async def enqueue_for_retry(
        self,
        atom_id: UUID,
        request_id: str,
        tool_name: str,
        tool_arguments: Dict[str, Any],
        runway_type: str,
        error: str,
        retry_count: int = 0,
    ) -> Optional[str]:
        """
        Add a failed atom to the retry queue.

        Args:
            atom_id: Atom UUID
            request_id: Request identifier
            tool_name: Tool name
            tool_arguments: Tool arguments (for retry)
            runway_type: CODE_TOOL|AI_AGENT|WORKFLOW
            error: Error message
            retry_count: Current retry count

        Returns:
            Queue entry ID or None
        """
        if retry_count >= self.config.MAX_RETRIES:
            logger.warning(
                f"[RetryEngine] Atom {atom_id} exhausted retries ({retry_count}). "
                f"Moving to dead letter queue."
            )
            return None

        try:
            response = self.supabase.rpc(
                "enqueue_retry",
                {
                    "p_atom_id": str(atom_id),
                    "p_request_id": request_id,
                    "p_org_id": self.org_id,
                    "p_workspace_id": self.workspace_id,
                    "p_tool_name": tool_name,
                    "p_tool_arguments": tool_arguments,
                    "p_runway_type": runway_type,
                    "p_last_error": error,
                    "p_retry_count": retry_count,
                    "p_max_retries": self.config.MAX_RETRIES,
                },
            ).execute()

            queue_id = response.data
            backoff_seconds = self._calculate_backoff(retry_count)
            logger.info(
                f"[RetryEngine] Enqueued atom {atom_id} for retry #{retry_count + 1} "
                f"in {backoff_seconds}s (queue_id={queue_id})"
            )
            return queue_id

        except Exception as e:
            logger.error(
                f"[RetryEngine] Failed to enqueue atom {atom_id} for retry: {e}",
                exc_info=True,
            )
            return None

    async def get_pending_retries(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Fetch atoms ready for retry (next_retry_at <= NOW).

        Args:
            limit: Maximum number of atoms to return

        Returns:
            List of retry queue entries
        """
        try:
            response = self.supabase.rpc(
                "get_pending_retries",
                {
                    "p_org_id": self.org_id,
                    "p_workspace_id": self.workspace_id,
                    "p_limit": limit,
                },
            ).execute()

            pending = response.data or []
            logger.info(f"[RetryEngine] Found {len(pending)} atoms ready for retry")
            return pending

        except Exception as e:
            logger.error(
                f"[RetryEngine] Failed to fetch pending retries: {e}", exc_info=True
            )
            return []

    async def mark_retry_success(self, queue_id: UUID) -> bool:
        """
        Mark a retry as successful (remove from queue).

        Args:
            queue_id: Retry queue entry ID

        Returns:
            True if updated successfully
        """
        try:
            self.supabase.table("retry_queue").update(
                {"status": "SUCCESS", "updated_at": datetime.utcnow().isoformat()}
            ).eq("id", str(queue_id)).execute()

            logger.info(f"[RetryEngine] Marked retry {queue_id} as SUCCESS")
            return True

        except Exception as e:
            logger.error(
                f"[RetryEngine] Failed to mark retry {queue_id} as success: {e}",
                exc_info=True,
            )
            return False

    async def mark_retry_exhausted(self, queue_id: UUID) -> bool:
        """
        Mark a retry as exhausted (dead letter queue).

        Args:
            queue_id: Retry queue entry ID

        Returns:
            True if updated successfully
        """
        try:
            self.supabase.table("retry_queue").update(
                {"status": "EXHAUSTED", "updated_at": datetime.utcnow().isoformat()}
            ).eq("id", str(queue_id)).execute()

            logger.warning(f"[RetryEngine] Moved retry {queue_id} to dead letter queue")
            return True

        except Exception as e:
            logger.error(
                f"[RetryEngine] Failed to mark retry {queue_id} as exhausted: {e}",
                exc_info=True,
            )
            return False

    def _calculate_backoff(self, retry_count: int) -> int:
        """
        Calculate exponential backoff in seconds.

        Args:
            retry_count: Current retry count (0-based)

        Returns:
            Backoff time in seconds
        """
        backoff = self.config.INITIAL_BACKOFF_SECONDS * (
            self.config.BACKOFF_MULTIPLIER**retry_count
        )
        return min(backoff, self.config.MAX_BACKOFF_SECONDS)

    def is_retryable_error(self, error_message: str) -> bool:
        """
        Determine if an error is retryable.

        Per spec: Network errors, timeouts, rate limits are retryable.
        Auth errors, validation errors are not retryable.

        Args:
            error_message: Error message string

        Returns:
            True if error is retryable
        """
        error_lower = error_message.lower()

        # Retryable patterns
        retryable_keywords = [
            "timeout",
            "network",
            "connection",
            "temporarily",
            "unavailable",
            "rate limit",
            "503",
            "502",
            "504",
        ]

        # Non-retryable patterns
        permanent_keywords = [
            "authentication",
            "authorization",
            "permission",
            "invalid",
            "not found",
            "400",
            "401",
            "403",
            "404",
        ]

        # Check for permanent errors first
        if any(keyword in error_lower for keyword in permanent_keywords):
            return False

        # Check for retryable errors
        return any(keyword in error_lower for keyword in retryable_keywords)

    async def process_retry_queue(
        self, executor_callback, limit: int = 10
    ) -> Dict[str, int]:
        """
        Process pending retries from the queue.

        Args:
            executor_callback: Async function(atom_dict) -> result_dict
            limit: Maximum number of retries to process

        Returns:
            Dictionary with success/failure counts
        """
        pending = await self.get_pending_retries(limit)

        if not pending:
            logger.debug("[RetryEngine] No pending retries to process")
            return {"processed": 0, "success": 0, "failed": 0}

        logger.info(f"[RetryEngine] Processing {len(pending)} pending retries")

        success_count = 0
        failure_count = 0

        for retry_entry in pending:
            queue_id = retry_entry["id"]
            atom_id = retry_entry["atom_id"]
            retry_count = retry_entry["retry_count"]

            try:
                # Execute the retry using provided callback
                result = await executor_callback(retry_entry)

                if result.get("status") == "SUCCESS":
                    await self.mark_retry_success(queue_id)
                    success_count += 1
                else:
                    # Re-enqueue if retries remaining
                    if retry_count + 1 < self.config.MAX_RETRIES:
                        await self.enqueue_for_retry(
                            atom_id=UUID(atom_id),
                            request_id=retry_entry["request_id"],
                            tool_name=retry_entry["tool_name"],
                            tool_arguments=retry_entry["tool_arguments"],
                            runway_type=retry_entry["runway_type"],
                            error=result.get("error", "Unknown error"),
                            retry_count=retry_count + 1,
                        )
                    else:
                        await self.mark_retry_exhausted(queue_id)

                    failure_count += 1

            except Exception as e:
                logger.error(
                    f"[RetryEngine] Error processing retry {queue_id}: {e}",
                    exc_info=True,
                )
                failure_count += 1

        return {
            "processed": len(pending),
            "success": success_count,
            "failed": failure_count,
        }
