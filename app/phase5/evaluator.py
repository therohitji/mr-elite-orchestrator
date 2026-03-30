"""
Phase 5: Main Evaluator (Orchestrator)

This module orchestrates the evaluation process after execution.
It coordinates validation, MDS calculation, analytics logging, and learning.

Flow:
  1. Validate execution results (SessionValidator)
  2. Calculate MDS (MDSEngine)
  3. Log analytics (AnalyticsStore)
  4. Run learning engines (SmartRecombinator) - async background

Per spec: "The Review Meeting" after work is done.
"""

import logging
import asyncio
from typing import Dict, List, Any, Optional
from datetime import datetime

from .validator import SessionValidator, ValidationResult
from .mds_engine import MDSEngine, MDSCalculation
from .analytics_store import AnalyticsStore
from .smart_recommender import SmartRecombinator

logger = logging.getLogger(__name__)


class EvaluationResult:
    """Complete evaluation result"""

    def __init__(
        self,
        session_id: str,
        validation: ValidationResult,
        mds_calculation: MDSCalculation,
        analytics_logged: bool = False,
        session_recorded: bool = False,
    ):
        self.session_id = session_id
        self.validation = validation
        self.mds_calculation = mds_calculation
        self.analytics_logged = analytics_logged
        self.session_recorded = session_recorded

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "session_id": self.session_id,
            "validation": self.validation.to_dict(),
            "mds": self.mds_calculation.to_dict(),
            "analytics_logged": self.analytics_logged,
            "session_recorded": self.session_recorded,
        }

    def get_summary_message(self) -> str:
        """Get human-readable summary"""
        status = self.validation.status.value
        minutes = self.mds_calculation.total_minutes_saved
        points = self.mds_calculation.total_mds_points

        if status == "COMPLETED":
            return (
                f"✅ Task completed successfully! "
                f"Saved {minutes:.1f} minutes (MDS: {points:.1f} points)"
            )
        elif status == "PARTIAL":
            return (
                f"⚠️ Task partially completed with {self.validation.failure_count} non-critical errors. "
                f"Saved {minutes:.1f} minutes."
            )
        elif status == "BLOCKED":
            return f"🚫 Task blocked: {self.validation.error_summary}"
        else:
            return f"❌ Task failed: {self.validation.error_summary}"


class EvaluationOrchestrator:
    """
    Main orchestrator for Phase 5 evaluation.

    Coordinates all evaluation components:
    - Validation
    - MDS calculation
    - Analytics logging
    - Smart recommendations (background)
    """

    def __init__(self, db_client):
        """
        Initialize evaluation orchestrator.

        Args:
            db_client: Supabase client
        """
        self.db = db_client

        # Initialize components
        self.validator = SessionValidator()
        self.mds_engine = MDSEngine(db_client)
        self.analytics_store = AnalyticsStore(db_client)
        self.smart_recommender = SmartRecombinator(db_client)

        logger.info("EvaluationOrchestrator initialized")

    async def evaluate_session(
        self,
        session_id: str,
        request_id: str,
        org_id: str,
        workspace_id: str,
        user_id: str,
        user_goal: str,
        atoms: List[Any],
        execution_results: List[Dict[str, Any]],
        start_time: datetime,
        end_time: Optional[datetime] = None,
    ) -> EvaluationResult:
        """
        Evaluate a completed execution session.

        This is the main entry point called after Phase 4 execution.

        Args:
            session_id: Unique session identifier
            request_id: Request ID
            org_id: Organization ID
            workspace_id: Workspace ID
            user_id: User ID
            user_goal: Original user request
            atoms: List of atoms that were executed
            execution_results: Results from Phase 4
            start_time: When execution started
            end_time: When execution ended

        Returns:
            EvaluationResult with all metrics
        """
        if not end_time:
            end_time = datetime.now()

        duration = (end_time - start_time).total_seconds()

        # Silently evaluate
        try:
            # Step 1: Validate execution results
            validation = await self.validator.validate(
                session_id=session_id, atoms=atoms, execution_results=execution_results
            )

            # Step 2: Calculate MDS (only if session succeeded or partially succeeded)
            if validation.status.value in ["COMPLETED", "PARTIAL"]:
                mds = await self.mds_engine.calculate_impact(
                    session_id=session_id,
                    atoms=atoms,
                    execution_results=execution_results,
                )
            else:
                # Failed session: no value delivered
                mds = MDSCalculation(
                    session_id=session_id,
                    total_minutes_saved=0.0,
                    total_mds_points=0.0,
                    category_breakdown={},
                    atoms_executed=len(atoms),
                    atoms_succeeded=validation.success_count,
                    atoms_failed=validation.failure_count,
                    cost_usd=0.0,
                    tokens_used=0,
                )

            # Step 3: Log analytics event
            analytics_logged = False
            if validation.status.value in ["COMPLETED", "PARTIAL"]:
                event_id = await self.analytics_store.log_analytics_event(
                    org_id=org_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    mds_calculation=mds.to_dict(),
                )
                analytics_logged = event_id is not None

            # Step 4: Record session summary
            session_recorded = False
            session_record_id = await self.analytics_store.record_session_summary(
                session_id=session_id,
                request_id=request_id,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
                user_goal=user_goal,
                total_atoms=len(atoms),
                validation_result=validation.to_dict(),
                duration_seconds=duration,
            )
            session_recorded = session_record_id is not None

            # Step 5: Background learning DISABLED (Safe Mode per approved spec)
            # SmartRecombinator learning loop is disabled to prevent drift
            # asyncio.create_task(self._run_background_learning(org_id, workspace_id))
            logger.info("[Phase5] Background learning skipped (Safe Mode)")

            # Build result
            result = EvaluationResult(
                session_id=session_id,
                validation=validation,
                mds_calculation=mds,
                analytics_logged=analytics_logged,
                session_recorded=session_recorded,
            )

            return result

        except Exception as e:
            logger.error(f"Evaluation failed for session {session_id}: {e}")

            # Return minimal result on failure
            return EvaluationResult(
                session_id=session_id,
                validation=ValidationResult(
                    status="FAILED", error_summary=f"Evaluation error: {str(e)}"
                ),
                mds_calculation=MDSCalculation(
                    session_id=session_id,
                    total_minutes_saved=0.0,
                    total_mds_points=0.0,
                    category_breakdown={},
                    atoms_executed=0,
                    atoms_succeeded=0,
                    atoms_failed=0,
                    cost_usd=0.0,
                    tokens_used=0,
                ),
            )

    async def _run_background_learning(self, org_id: str, workspace_id: str):
        """
        Run smart recommendation engines in background.

        This doesn't block the main flow - runs asynchronously.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
        """
        try:
            logger.info(f"Background learning started for org {org_id}")

            # Run all three engines
            results = await self.smart_recommender.run_all_engines(
                org_id=org_id, workspace_id=workspace_id, days=30
            )

            logger.info(f"Background learning complete: {results}")

        except Exception as e:
            logger.error(f"Background learning failed: {e}")

    async def get_user_dashboard(
        self, org_id: str, workspace_id: str, user_id: str
    ) -> Dict[str, Any]:
        """
        Get dashboard data for user.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            user_id: User ID

        Returns:
            Dashboard data dict
        """
        try:
            # Get analytics summary
            analytics = await self.analytics_store.get_user_analytics(
                org_id=org_id, workspace_id=workspace_id, user_id=user_id
            )

            # Get category breakdown
            categories = await self.analytics_store.get_category_breakdown(
                org_id=org_id, workspace_id=workspace_id, user_id=user_id, days=30
            )

            # Get recent sessions
            recent_sessions = await self.analytics_store.get_recent_sessions(
                org_id=org_id, workspace_id=workspace_id, limit=10
            )

            # Get pending recommendations
            recommendations_response = self.db.rpc(
                "get_pending_recommendations",
                {"p_org_id": org_id, "p_workspace_id": workspace_id, "p_limit": 5},
            ).execute()

            recommendations = recommendations_response.data or []

            return {
                "analytics": analytics or {},
                "category_breakdown": categories,
                "recent_sessions": recent_sessions,
                "recommendations": recommendations,
                "generated_at": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"Failed to get dashboard: {e}")
            return {
                "analytics": {},
                "category_breakdown": {},
                "recent_sessions": [],
                "recommendations": [],
                "error": str(e),
            }

    async def apply_recommendation(
        self, rec_id: str, reviewed_by: str, org_id: str, workspace_id: str
    ) -> bool:
        """
        Apply a smart recommendation.

        Per spec: When user clicks "Accept", physically update
        the system (workflow registry, policies, prompts).

        Args:
            rec_id: Recommendation ID
            reviewed_by: User who approved
            org_id: Organization ID
            workspace_id: Workspace ID

        Returns:
            True if applied successfully
        """
        try:
            # Fetch recommendation
            rec_response = (
                self.db.table("smart_recommendations")
                .select("*")
                .eq("id", rec_id)
                .single()
                .execute()
            )

            rec = rec_response.data

            if not rec or rec["status"] != "PENDING":
                logger.warning(f"Recommendation {rec_id} not found or not pending")
                return False

            rec_type = rec["rec_type"]
            proposal_data = rec["proposal_data"]

            # Apply based on type
            if rec_type == "WORKFLOW_DISCOVERY":
                # Add to workflow registry
                success = await self._apply_workflow_recommendation(
                    org_id, workspace_id, proposal_data
                )

            elif rec_type == "POLICY_UPDATE":
                # Add to organization policies
                success = await self._apply_policy_recommendation(
                    org_id, workspace_id, proposal_data
                )

            elif rec_type == "PROMPT_OPTIMIZATION":
                # Update agent prompt
                success = await self._apply_prompt_recommendation(
                    org_id, workspace_id, proposal_data
                )

            else:
                logger.warning(f"Unknown recommendation type: {rec_type}")
                return False

            if success:
                # Mark as applied
                self.db.rpc(
                    "apply_recommendation",
                    {"p_rec_id": rec_id, "p_reviewed_by": reviewed_by},
                ).execute()

                logger.info(f"Applied recommendation: {rec_id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to apply recommendation: {e}")
            return False

    async def _apply_workflow_recommendation(
        self, org_id: str, workspace_id: str, proposal: Dict[str, Any]
    ) -> bool:
        """Apply workflow discovery recommendation"""
        # This would integrate with workflow registry
        # Placeholder for now
        logger.info(f"Would create workflow: {proposal.get('workflow_name')}")
        return True

    async def _apply_policy_recommendation(
        self, org_id: str, workspace_id: str, proposal: Dict[str, Any]
    ) -> bool:
        """Apply policy update recommendation"""
        # This would integrate with Phase 3 policy engine
        # Placeholder for now
        logger.info(f"Would create policy: {proposal.get('new_rule')}")
        return True

    async def _apply_prompt_recommendation(
        self, org_id: str, workspace_id: str, proposal: Dict[str, Any]
    ) -> bool:
        """Apply prompt optimization recommendation"""
        # This would update agent registry prompts
        # Placeholder for now
        logger.info(f"Would update prompt for: {proposal.get('agent')}")
        return True


# Factory function for easy instantiation
def create_evaluator(db_client) -> EvaluationOrchestrator:
    """
    Create an evaluation orchestrator instance.

    Args:
        db_client: Supabase client

    Returns:
        EvaluationOrchestrator instance
    """
    return EvaluationOrchestrator(db_client)
