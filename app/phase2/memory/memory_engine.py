import asyncio
import logging
from typing import Dict, Any, List, Optional
from app.phase1.atoms import ExecutionAtom
from app.phase2.contracts import CheckResult

logger = logging.getLogger(__name__)


class MemoryEngine:
    """
    Phase 2 Memory Layer: Episodic + Reflective Memory

    Episodic: Past actions (decisions made, resources used)
    Reflective: Learned patterns (what worked, what didn't)

    Memory is INFORMATIONAL only - generates warnings, not blocks
    """

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client

    async def fetch_memory(
        self, atom: ExecutionAtom, workspace_id: str
    ) -> Dict[str, Any]:
        """
        Retrieve relevant memory for atom decision context.

        Returns: {
            "episodic": [past_actions],
            "reflective": [learned_patterns],
            "warnings": [recommended_cautions],
            "status": "found" | "not_found"
        }
        """
        logger.info(f"[Memory] Fetching for atom {atom.id}")

        try:
            # Episodic memory: past similar actions
            episodic = await self._fetch_episodic_memory(atom, workspace_id)

            # Reflective memory: learned patterns
            reflective = await self._fetch_reflective_memory(atom, workspace_id)

            # Generate warnings based on memory
            warnings = self._generate_warnings(episodic, reflective)

            return {
                "episodic": episodic,
                "reflective": reflective,
                "warnings": warnings,
                "status": "found" if (episodic or reflective) else "not_found",
            }

        except Exception as e:
            logger.warning(f"[Memory] Fetch failed: {e}")
            return {
                "episodic": [],
                "reflective": [],
                "warnings": [],
                "status": "error",
                "error": str(e),
            }

    async def _fetch_episodic_memory(
        self, atom: ExecutionAtom, workspace_id: str
    ) -> List[Dict]:
        """
        Episodic: Query recent similar actions from atom history.

        Simulates querying decision log for similar atoms:
        "Last time we tried to draft offer for director, it took 2 hours to approve"
        """
        # In real implementation: Query audit_logs table for similar atoms
        # For now: Return pattern-based memory

        episodic_patterns = []

        if atom.tool == "draft_offer":
            episodic_patterns.append(
                {
                    "action": "draft_offer",
                    "count": 12,
                    "avg_approval_time": "45 minutes",
                    "success_rate": "95%",
                    "common_issues": [
                        "Salary negotiation delays",
                        "Equity vesting mismatch",
                    ],
                }
            )
        elif atom.tool == "schedule_interview":
            episodic_patterns.append(
                {
                    "action": "schedule_interview",
                    "count": 45,
                    "avg_scheduling_time": "15 minutes",
                    "success_rate": "98%",
                    "common_issues": ["Timezone conflicts"],
                }
            )

        return episodic_patterns

    async def _fetch_reflective_memory(
        self, atom: ExecutionAtom, workspace_id: str
    ) -> List[Dict]:
        """
        Reflective: Learned patterns about this type of action.

        "We learned that offers below $100k require director approval"
        "We learned that director-level roles need founder sign-off"
        """

        reflective_patterns = []

        if atom.tool == "draft_offer":
            reflective_patterns.append(
                {
                    "pattern": "director_level_offers_need_founder_approval",
                    "condition": "level == 'Director' or level == 'VP'",
                    "action": "require_founder_review",
                    "confidence": 0.92,
                    "learned_from": "15 recent offers",
                }
            )
            reflective_patterns.append(
                {
                    "pattern": "high_equity_awards_delay_process",
                    "condition": "equity_percentage > 0.5",
                    "action": "alert_finance_early",
                    "confidence": 0.87,
                    "learned_from": "8 recent offers",
                }
            )

        return reflective_patterns

    def _generate_warnings(
        self, episodic: List[Dict], reflective: List[Dict]
    ) -> List[str]:
        """
        Generate informational warnings (not blocks) based on memory.
        """
        warnings = []

        if episodic and episodic[0].get("common_issues"):
            for issue in episodic[0].get("common_issues", []):
                warnings.append(f"⚠️  Common issue: {issue}")

        if reflective:
            for pattern in reflective:
                confidence = pattern.get("confidence", 0)
                if confidence > 0.8:
                    action = pattern.get("action", "")
                    warnings.append(f"💡 Recommended: {action}")

        return warnings


async def fetch_memory(
    atom: ExecutionAtom, workspace_id: str = "default"
) -> Dict[str, Any]:
    """
    Async wrapper for memory fetching.
    """
    engine = MemoryEngine()
    return await engine.fetch_memory(atom, workspace_id)


def fetch_memory_sync(
    atom: ExecutionAtom, workspace_id: str = "default"
) -> Dict[str, Any]:
    """
    Synchronous wrapper for legacy code.
    """
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(fetch_memory(atom, workspace_id))
    except RuntimeError:
        return asyncio.run(fetch_memory(atom, workspace_id))
