"""
Phase 5: Analytics Store

This module handles database persistence for evaluation metrics.
It stores MDS calculations, session summaries, and analytics events.

Per spec:
  - Log analytics events to user_analytics_ledger
  - Record session summaries to execution_sessions
  - Query user statistics
  - Non-fatal storage (never crashes)
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


class AnalyticsStore:
    """
    Database persistence for evaluation metrics.

    Stores MDS calculations, session summaries, and provides
    query methods for dashboard analytics.
    """

    def __init__(self, db_client):
        """
        Initialize analytics store.

        Args:
            db_client: Supabase client
        """
        self.db = db_client
        logger.info("AnalyticsStore initialized")

    async def log_analytics_event(
        self,
        org_id: str,
        workspace_id: str,
        user_id: str,
        session_id: str,
        request_id: str,
        mds_calculation: Dict[str, Any],
    ) -> Optional[str]:
        """
        Log an analytics event to the ledger.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            user_id: User ID
            session_id: Session ID
            request_id: Request ID
            mds_calculation: MDS calculation result

        Returns:
            Event ID or None if failed
        """
        try:
            # Determine skill category from breakdown
            category_breakdown = mds_calculation.get("category_breakdown", {})
            skill_category = (
                max(category_breakdown, key=category_breakdown.get)
                if category_breakdown
                else None
            )

            # Call RPC function
            response = self.db.rpc(
                "log_analytics_event",
                {
                    "p_org_id": org_id,
                    "p_workspace_id": workspace_id,
                    "p_user_id": user_id,
                    "p_session_id": session_id,
                    "p_request_id": request_id,
                    "p_minutes_saved": mds_calculation.get("total_minutes_saved", 0.0),
                    "p_mds_points": mds_calculation.get("total_mds_points", 0.0),
                    "p_atoms_executed": mds_calculation.get("atoms_executed", 0),
                    "p_atoms_succeeded": mds_calculation.get("atoms_succeeded", 0),
                    "p_atoms_failed": mds_calculation.get("atoms_failed", 0),
                    "p_tokens_used": mds_calculation.get("tokens_used", 0),
                    "p_cost_usd": mds_calculation.get("cost_usd", 0.0),
                    "p_skill_category": skill_category,
                },
            ).execute()

            event_id = response.data
            logger.info(f"Logged analytics event: {event_id}")
            return event_id

        except Exception as e:
            logger.error(f"Failed to log analytics event: {e}")
            return None

    async def record_session_summary(
        self,
        session_id: str,
        request_id: str,
        org_id: str,
        workspace_id: str,
        user_id: str,
        user_goal: str,
        total_atoms: int,
        validation_result: Dict[str, Any],
        duration_seconds: float,
    ) -> Optional[str]:
        """
        Record session summary to database.

        Args:
            session_id: Unique session ID
            request_id: Request ID
            org_id: Organization ID
            workspace_id: Workspace ID
            user_id: User ID
            user_goal: User's original request
            total_atoms: Number of atoms in plan
            validation_result: ValidationResult dict
            duration_seconds: Total execution time

        Returns:
            Session record ID or None
        """
        try:
            response = self.db.rpc(
                "record_session_summary",
                {
                    "p_session_id": session_id,
                    "p_request_id": request_id,
                    "p_org_id": org_id,
                    "p_workspace_id": workspace_id,
                    "p_user_id": user_id,
                    "p_user_goal": user_goal,
                    "p_total_atoms": total_atoms,
                    "p_final_status": validation_result.get("status", "FAILED"),
                    "p_error_summary": validation_result.get("error_summary"),
                    "p_success_count": validation_result.get("success_count", 0),
                    "p_failure_count": validation_result.get("failure_count", 0),
                    "p_duration_seconds": duration_seconds,
                },
            ).execute()

            record_id = response.data
            logger.info(f"Recorded session summary: {record_id}")
            return record_id

        except Exception as e:
            logger.error(f"Failed to record session summary: {e}")
            return None

    async def get_user_analytics(
        self, org_id: str, workspace_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get analytics summary for a user.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            user_id: User ID

        Returns:
            Analytics summary dict or None
        """
        try:
            response = self.db.rpc(
                "get_user_analytics",
                {
                    "p_org_id": org_id,
                    "p_workspace_id": workspace_id,
                    "p_user_id": user_id,
                },
            ).execute()

            if response.data:
                data = (
                    response.data[0]
                    if isinstance(response.data, list)
                    else response.data
                )
                return {
                    "total_sessions": int(data.get("total_sessions", 0)),
                    "total_minutes_saved": float(data.get("total_minutes_saved", 0.0)),
                    "total_mds_points": float(data.get("total_mds_points", 0.0)),
                    "total_atoms_executed": int(data.get("total_atoms_executed", 0)),
                    "total_atoms_succeeded": int(data.get("total_atoms_succeeded", 0)),
                    "total_atoms_failed": int(data.get("total_atoms_failed", 0)),
                    "avg_minutes_per_session": float(
                        data.get("avg_minutes_per_session", 0.0)
                    ),
                    "total_cost_usd": float(data.get("total_cost_usd", 0.0)),
                    "last_activity": data.get("last_activity"),
                }

            return None

        except Exception as e:
            logger.error(f"Failed to get user analytics: {e}")
            return None

    async def get_recent_sessions(
        self, org_id: str, workspace_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get recent execution sessions.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            limit: Maximum number of sessions

        Returns:
            List of session summaries
        """
        try:
            response = (
                self.db.table("execution_sessions")
                .select("*")
                .eq("org_id", org_id)
                .eq("workspace_id", workspace_id)
                .order("start_time", desc=True)
                .limit(limit)
                .execute()
            )

            return response.data or []

        except Exception as e:
            logger.error(f"Failed to get recent sessions: {e}")
            return []

    async def get_category_breakdown(
        self, org_id: str, workspace_id: str, user_id: str, days: int = 30
    ) -> Dict[str, float]:
        """
        Get time saved by category.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            user_id: User ID
            days: Number of days to analyze

        Returns:
            Dict of category -> minutes saved
        """
        try:
            # Query ledger for recent events
            response = (
                self.db.table("user_analytics_ledger")
                .select("skill_category, minutes_saved")
                .eq("org_id", org_id)
                .eq("workspace_id", workspace_id)
                .eq("user_id", user_id)
                .gte("created_at", f"now() - interval '{days} days'")
                .execute()
            )

            # Aggregate by category
            breakdown: Dict[str, float] = {}
            for row in response.data:
                category = row.get("skill_category", "GENERAL")
                minutes = row.get("minutes_saved", 0.0)
                breakdown[category] = breakdown.get(category, 0.0) + minutes

            return breakdown

        except Exception as e:
            logger.error(f"Failed to get category breakdown: {e}")
            return {}

    async def record_user_feedback(
        self,
        org_id: str,
        workspace_id: str,
        user_id: str,
        atom_id: str,
        session_id: str,
        feedback_type: str,
        original_output: Any = None,
        edited_output: Any = None,
        rating: Optional[int] = None,
        comment: Optional[str] = None,
    ) -> Optional[str]:
        """
        Record user feedback for learning.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            user_id: User ID
            atom_id: Atom ID that was edited
            session_id: Session ID
            feedback_type: EDIT, CORRECTION, RATING, COMMENT
            original_output: Original AI output
            edited_output: User's edited version
            rating: 1-5 rating
            comment: User comment

        Returns:
            Feedback ID or None
        """
        try:
            response = (
                self.db.table("user_feedback")
                .insert(
                    {
                        "org_id": org_id,
                        "workspace_id": workspace_id,
                        "user_id": user_id,
                        "atom_id": atom_id,
                        "session_id": session_id,
                        "feedback_type": feedback_type,
                        "original_output": original_output,
                        "edited_output": edited_output,
                        "user_rating": rating,
                        "user_comment": comment,
                    }
                )
                .execute()
            )

            feedback_id = response.data[0]["id"] if response.data else None
            logger.info(f"Recorded user feedback: {feedback_id}")
            return feedback_id

        except Exception as e:
            logger.error(f"Failed to record user feedback: {e}")
            return None
