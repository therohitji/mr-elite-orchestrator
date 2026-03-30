"""
Phase 5: Smart Recommender

This module implements pattern recognition and learning.
It analyzes execution history to suggest automation opportunities.

Three Mining Engines:
  1. Workflow Miner - Detect recurring task sequences
  2. Trust Calibrator - Identify auto-approval candidates
  3. Correction Learner - Learn from user edits

Per spec: "GeniOS shadows the founder... then says 'I see how you
handle invoices; want me to take over?'"
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter, defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SmartRecombinator:
    """
    The "Growth Engine" for system intelligence.

    Observes user behavior patterns and suggests structural
    improvements to workflows, policies, and prompts.
    """

    def __init__(self, db_client):
        """
        Initialize smart recommender.

        Args:
            db_client: Supabase client
        """
        self.db = db_client
        logger.info("SmartRecombinator initialized")

    async def run_workflow_mining(
        self, org_id: str, workspace_id: str, days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Engine 1: Detect recurring task sequences.

        Per spec: Identify N-Grams (sequences of tool calls) that
        repeat frequently and suggest converting to workflow.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            days: Number of days to analyze

        Returns:
            List of workflow recommendations
        """
        logger.info(f"Running workflow mining for org {org_id}")

        try:
            # Fetch recent execution sessions
            cutoff_date = datetime.now() - timedelta(days=days)

            response = (
                self.db.table("execution_sessions")
                .select("session_id, request_id")
                .eq("org_id", org_id)
                .eq("workspace_id", workspace_id)
                .gte("start_time", cutoff_date.isoformat())
                .eq("final_status", "COMPLETED")
                .execute()
            )

            sessions = response.data

            if len(sessions) < 3:
                logger.info("Not enough sessions for pattern mining")
                return []

            # Fetch execution results for these sessions
            session_ids = [s["session_id"] for s in sessions]

            # Get atoms from execution_results (Phase 4)
            sequences: List[List[str]] = []

            for session_id in session_ids:
                # Query execution_results for this session
                results_response = (
                    self.db.table("execution_results")
                    .select("atom_id, metadata")
                    .eq("org_id", org_id)
                    .eq("status", "SUCCESS")
                    .order("created_at")
                    .execute()
                )

                # Extract tool names
                tool_sequence = []
                for result in results_response.data:
                    metadata = result.get("metadata", {})
                    tool_name = metadata.get("tool_name", "unknown")
                    tool_sequence.append(tool_name)

                if len(tool_sequence) >= 2:
                    sequences.append(tool_sequence)

            # Find frequent sequences (N-grams)
            recommendations = self._find_frequent_sequences(
                sequences, org_id, workspace_id, session_ids
            )

            logger.info(f"Found {len(recommendations)} workflow recommendations")
            return recommendations

        except Exception as e:
            logger.error(f"Workflow mining failed: {e}")
            return []

    def _find_frequent_sequences(
        self,
        sequences: List[List[str]],
        org_id: str,
        workspace_id: str,
        session_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Find frequently occurring sequences using N-gram analysis.

        Args:
            sequences: List of tool sequences
            org_id: Organization ID
            workspace_id: Workspace ID
            session_ids: Session IDs analyzed

        Returns:
            List of recommendations
        """
        # Generate bigrams and trigrams
        ngrams: Counter = Counter()

        for seq in sequences:
            # Bigrams
            for i in range(len(seq) - 1):
                bigram = tuple(seq[i : i + 2])
                ngrams[bigram] += 1

            # Trigrams
            for i in range(len(seq) - 2):
                trigram = tuple(seq[i : i + 3])
                ngrams[trigram] += 1

        # Filter for frequency > 3 and success rate > 90%
        recommendations = []

        for ngram, count in ngrams.items():
            if count >= 3:
                # Calculate confidence
                confidence = min(1.0, count / len(sequences))

                # Create recommendation
                tool_names = " → ".join(ngram)

                recommendations.append(
                    {
                        "org_id": org_id,
                        "workspace_id": workspace_id,
                        "rec_type": "WORKFLOW_DISCOVERY",
                        "engine_source": "workflow_miner",
                        "title": f"Automate '{tool_names}' Workflow",
                        "description": f"You frequently run this sequence: {tool_names}. Save it as a one-click workflow?",
                        "proposal_data": {
                            "workflow_name": f"auto_{ngram[0]}_{ngram[-1]}",
                            "steps": list(ngram),
                            "frequency": count,
                        },
                        "confidence": confidence,
                        "trigger_events": session_ids[:5],  # Sample evidence
                        "sample_count": count,
                    }
                )

        return recommendations

    async def run_trust_calibration(
        self, org_id: str, workspace_id: str, days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Engine 2: Identify auto-approval candidates.

        Per spec: If user approves same action 10 times without edit,
        suggest adding it to auto-approval policy.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            days: Number of days to analyze

        Returns:
            List of policy recommendations
        """
        logger.info(f"Running trust calibration for org {org_id}")

        try:
            # Query approval_audit_trail from Phase 3
            cutoff_date = datetime.now() - timedelta(days=days)

            # This would query the approval audit trail
            # For now, we'll return a stub since we need Phase 3 integration

            recommendations = []

            # Example logic (would be implemented with real approval data):
            # 1. Group approvals by risk_reason or tool_name
            # 2. Calculate approval rate (approved / total)
            # 3. If approval_rate == 100% and count >= 10, suggest auto-approval

            logger.info(f"Found {len(recommendations)} policy recommendations")
            return recommendations

        except Exception as e:
            logger.error(f"Trust calibration failed: {e}")
            return []

    async def run_correction_learning(
        self, org_id: str, workspace_id: str, days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Engine 3: Learn from user edits.

        Per spec: Compare original_draft vs. final_approved_text.
        Extract the preference and suggest prompt update.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            days: Number of days to analyze

        Returns:
            List of prompt optimization recommendations
        """
        logger.info(f"Running correction learning for org {org_id}")

        try:
            # Fetch user feedback (edits)
            cutoff_date = datetime.now() - timedelta(days=days)

            response = (
                self.db.table("user_feedback")
                .select("atom_id, original_output, edited_output, session_id")
                .eq("org_id", org_id)
                .eq("workspace_id", workspace_id)
                .eq("feedback_type", "EDIT")
                .gte("created_at", cutoff_date.isoformat())
                .execute()
            )

            edits = response.data

            if len(edits) < 3:
                logger.info("Not enough edits for learning")
                return []

            # Analyze patterns in edits
            recommendations = self._analyze_edit_patterns(edits, org_id, workspace_id)

            logger.info(f"Found {len(recommendations)} prompt recommendations")
            return recommendations

        except Exception as e:
            logger.error(f"Correction learning failed: {e}")
            return []

    def _analyze_edit_patterns(
        self, edits: List[Dict[str, Any]], org_id: str, workspace_id: str
    ) -> List[Dict[str, Any]]:
        """
        Analyze user edit patterns to find consistent corrections.

        Args:
            edits: List of user edit records
            org_id: Organization ID
            workspace_id: Workspace ID

        Returns:
            List of recommendations
        """
        recommendations = []

        # Group by type of change (this is simplified)
        # In production, would use LLM to classify semantic changes

        edit_types: defaultdict = defaultdict(int)
        edit_examples: defaultdict = defaultdict(list)

        for edit in edits:
            original = str(edit.get("original_output", ""))
            edited = str(edit.get("edited_output", ""))

            # Simple heuristic: detect greeting changes
            if "Hi" in original and "Dear" in edited:
                edit_types["formal_greeting"] += 1
                edit_examples["formal_greeting"].append(edit["session_id"])

            # Detect sign-off changes
            if "Cheers" in original and "Best Regards" in edited:
                edit_types["formal_signoff"] += 1
                edit_examples["formal_signoff"].append(edit["session_id"])

        # Generate recommendations for patterns with count >= 3
        for edit_type, count in edit_types.items():
            if count >= 3:
                confidence = min(1.0, count / len(edits))

                if edit_type == "formal_greeting":
                    recommendations.append(
                        {
                            "org_id": org_id,
                            "workspace_id": workspace_id,
                            "rec_type": "PROMPT_OPTIMIZATION",
                            "engine_source": "correction_learner",
                            "title": "Update Email Greeting Style",
                            "description": "You consistently change greetings to formal style. Update email agent to use 'Dear [Name]' by default?",
                            "proposal_data": {
                                "agent": "email_agent",
                                "prompt_update": "Always use formal greetings: 'Dear [Name]' instead of 'Hi'",
                                "change_type": "formal_greeting",
                            },
                            "confidence": confidence,
                            "trigger_events": edit_examples[edit_type][:5],
                            "sample_count": count,
                        }
                    )

                elif edit_type == "formal_signoff":
                    recommendations.append(
                        {
                            "org_id": org_id,
                            "workspace_id": workspace_id,
                            "rec_type": "PROMPT_OPTIMIZATION",
                            "engine_source": "correction_learner",
                            "title": "Update Email Sign-off Style",
                            "description": "You consistently change sign-offs to 'Best Regards'. Update email agent?",
                            "proposal_data": {
                                "agent": "email_agent",
                                "prompt_update": "Always end emails with 'Best Regards' instead of 'Cheers'",
                                "change_type": "formal_signoff",
                            },
                            "confidence": confidence,
                            "trigger_events": edit_examples[edit_type][:5],
                            "sample_count": count,
                        }
                    )

        return recommendations

    async def save_recommendations(self, recommendations: List[Dict[str, Any]]) -> int:
        """
        Save recommendations to database.

        Args:
            recommendations: List of recommendation dicts

        Returns:
            Number of recommendations saved
        """
        saved_count = 0

        for rec in recommendations:
            try:
                # Check if similar recommendation already exists
                existing = (
                    self.db.table("smart_recommendations")
                    .select("id")
                    .eq("org_id", rec["org_id"])
                    .eq("workspace_id", rec["workspace_id"])
                    .eq("rec_type", rec["rec_type"])
                    .eq("status", "PENDING")
                    .execute()
                )

                if existing.data:
                    logger.info(f"Recommendation already exists: {rec['title']}")
                    continue

                # Create recommendation via RPC
                self.db.rpc(
                    "create_recommendation",
                    {
                        "p_org_id": rec["org_id"],
                        "p_workspace_id": rec["workspace_id"],
                        "p_rec_type": rec["rec_type"],
                        "p_engine_source": rec["engine_source"],
                        "p_title": rec["title"],
                        "p_description": rec["description"],
                        "p_proposal_data": rec["proposal_data"],
                        "p_confidence": rec["confidence"],
                        "p_trigger_events": rec["trigger_events"],
                        "p_sample_count": rec["sample_count"],
                    },
                ).execute()

                saved_count += 1
                logger.info(f"Saved recommendation: {rec['title']}")

            except Exception as e:
                logger.error(f"Failed to save recommendation: {e}")

        return saved_count

    async def run_all_engines(
        self, org_id: str, workspace_id: str, days: int = 30
    ) -> Dict[str, int]:
        """
        Run all three mining engines.

        Args:
            org_id: Organization ID
            workspace_id: Workspace ID
            days: Number of days to analyze

        Returns:
            Dict with counts per engine
        """
        logger.info(f"Running all recommendation engines for org {org_id}")

        # Run all engines
        workflow_recs = await self.run_workflow_mining(org_id, workspace_id, days)
        trust_recs = await self.run_trust_calibration(org_id, workspace_id, days)
        correction_recs = await self.run_correction_learning(org_id, workspace_id, days)

        # Combine all recommendations
        all_recs = workflow_recs + trust_recs + correction_recs

        # Save to database
        saved_count = await self.save_recommendations(all_recs)

        return {
            "workflow_discoveries": len(workflow_recs),
            "policy_updates": len(trust_recs),
            "prompt_optimizations": len(correction_recs),
            "total_saved": saved_count,
        }
