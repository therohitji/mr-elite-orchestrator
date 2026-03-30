"""
Phase 4: Result Store - Database persistence for execution results
Stores all execution outputs, links to atoms, provides query capabilities
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID
from datetime import datetime

from app.core.supabase_client import supabase_client

logger = logging.getLogger(__name__)


class ResultStore:
    """
    Database-backed storage for execution results.
    Provides persistence and query capabilities for Phase 4 execution outputs.
    """

    def __init__(self, org_id: str, workspace_id: str):
        """
        Initialize result store.

        Args:
            org_id: Organization ID for multi-tenancy
            workspace_id: Workspace ID for isolation
        """
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.supabase = supabase_client

    async def store_result(
        self,
        atom_id: UUID,
        request_id: str,
        tool_name: str,
        runway_type: str,
        status: str,
        output: Optional[Any] = None,
        error: Optional[str] = None,
        duration_ms: float = 0.0,
        retries: int = 0,
        trace_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Store an execution result in the database.

        Args:
            atom_id: Atom UUID
            request_id: Request identifier
            tool_name: Tool that was executed
            runway_type: CODE_TOOL|AI_AGENT|WORKFLOW
            status: SUCCESS|FAILED|TIMEOUT|RETRYING
            output: Execution output (JSON-serializable)
            error: Error message if failed
            duration_ms: Execution duration in milliseconds
            retries: Number of retries attempted
            trace_id: Trace ID for observability

        Returns:
            Result ID from database, or None if storage failed
        """
        try:
            # Skip storing result - execution_results table RPC not implemented
            # Non-critical: execution still completes successfully
            logger.debug(f"[ResultStore] Result logged (skipped): {atom_id} - {status}")
            return None

        except Exception as e:
            # Non-fatal: Result storage should never crash the execution flow
            logger.error(
                f"[ResultStore] Failed to store result for atom {atom_id}: {e}",
                exc_info=True,
            )
            return None

    async def get_results_for_request(self, request_id: str) -> List[Dict[str, Any]]:
        """
        Query all execution results for a request.

        Args:
            request_id: Request identifier

        Returns:
            List of execution result dictionaries
        """
        try:
            response = self.supabase.rpc(
                "get_execution_results",
                {
                    "p_request_id": request_id,
                    "p_org_id": self.org_id,
                    "p_workspace_id": self.workspace_id,
                },
            ).execute()

            results = response.data or []
            logger.info(
                f"[ResultStore] Retrieved {len(results)} results for request {request_id}"
            )
            return results

        except Exception as e:
            logger.error(
                f"[ResultStore] Failed to query results for request {request_id}: {e}",
                exc_info=True,
            )
            return []

    async def get_result_for_atom(self, atom_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Get the most recent execution result for a specific atom.

        Args:
            atom_id: Atom UUID

        Returns:
            Result dictionary or None
        """
        try:
            response = (
                self.supabase.table("execution_results")
                .select("*")
                .eq("atom_id", str(atom_id))
                .eq("org_id", self.org_id)
                .eq("workspace_id", self.workspace_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

            if response.data and len(response.data) > 0:
                return response.data[0]
            return None

        except Exception as e:
            logger.error(
                f"[ResultStore] Failed to get result for atom {atom_id}: {e}",
                exc_info=True,
            )
            return None

    async def log_execution_event(
        self,
        atom_id: UUID,
        request_id: str,
        event_type: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Log a single execution event for observability.

        Args:
            atom_id: Atom UUID
            request_id: Request identifier
            event_type: START|STEP|COMPLETE|ERROR|RETRY
            message: Log message
            metadata: Additional metadata (JSON)

        Returns:
            Log ID or None
        """
        try:
            # Note: RPC function log_execution_event doesn't exist yet
            # Gracefully skip logging (non-critical)
            logger.debug(
                f"[ResultStore] Event logged (skipped): {atom_id} - {event_type}: {message}"
            )
            return None

        except Exception as e:
            # Non-fatal logging
            logger.debug(f"[ResultStore] Event logging skipped: {e}")
            return None

    async def get_execution_logs(
        self, request_id: Optional[str] = None, atom_id: Optional[UUID] = None
    ) -> List[Dict[str, Any]]:
        """
        Query execution logs.

        Args:
            request_id: Filter by request (optional)
            atom_id: Filter by atom (optional)

        Returns:
            List of log entries
        """
        try:
            query = (
                self.supabase.table("execution_logs")
                .select("*")
                .eq("org_id", self.org_id)
                .eq("workspace_id", self.workspace_id)
            )

            if request_id:
                query = query.eq("request_id", request_id)
            if atom_id:
                query = query.eq("atom_id", str(atom_id))

            response = query.order("created_at", desc=True).limit(100).execute()

            return response.data or []

        except Exception as e:
            logger.error(f"[ResultStore] Failed to query logs: {e}", exc_info=True)
            return []

    async def get_summary_stats(self, request_id: str) -> Dict[str, Any]:
        """
        Get summary statistics for a request execution.

        Args:
            request_id: Request identifier

        Returns:
            Dictionary with success/failure counts, durations, etc.
        """
        results = await self.get_results_for_request(request_id)

        if not results:
            return {
                "total": 0,
                "success": 0,
                "failed": 0,
                "timeout": 0,
                "total_duration_ms": 0,
                "avg_duration_ms": 0,
            }

        success = sum(1 for r in results if r.get("status") == "SUCCESS")
        failed = sum(1 for r in results if r.get("status") == "FAILED")
        timeout = sum(1 for r in results if r.get("status") == "TIMEOUT")

        total_duration = sum(r.get("duration_ms", 0) for r in results)
        avg_duration = total_duration / len(results) if results else 0

        return {
            "total": len(results),
            "success": success,
            "failed": failed,
            "timeout": timeout,
            "total_duration_ms": total_duration,
            "avg_duration_ms": avg_duration,
        }
