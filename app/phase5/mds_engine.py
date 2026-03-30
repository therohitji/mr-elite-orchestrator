"""
Phase 5: MDS Engine (Maturity Delta Score)

This module calculates the "Business Impact" metric.
It quantifies time saved by comparing manual effort vs AI execution time.

Formula: MDS = Σ(T_manual - T_execution) × W_complexity

Per spec:
  - Reference library of "standard human times"
  - Track AI execution times
  - Weight by task complexity
  - Output: minutes saved, MDS points

Used for:
  - Dashboard metrics ("You saved 45 minutes")
  - ROI calculations
  - Value tracking over time
"""

import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TaskBenchmark:
    """Reference time for a task type"""

    task_type: str
    category: str
    manual_minutes: float
    ai_minutes: float
    complexity_weight: float


@dataclass
class MDSCalculation:
    """Result of MDS calculation"""

    session_id: str
    total_minutes_saved: float
    total_mds_points: float
    category_breakdown: Dict[str, float]
    atoms_executed: int
    atoms_succeeded: int
    atoms_failed: int
    cost_usd: float
    tokens_used: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "session_id": self.session_id,
            "total_minutes_saved": round(self.total_minutes_saved, 2),
            "total_mds_points": round(self.total_mds_points, 2),
            "category_breakdown": {
                k: round(v, 2) for k, v in self.category_breakdown.items()
            },
            "atoms_executed": self.atoms_executed,
            "atoms_succeeded": self.atoms_succeeded,
            "atoms_failed": self.atoms_failed,
            "cost_usd": round(self.cost_usd, 4),
            "tokens_used": self.tokens_used,
        }


class MDSEngine:
    """
    The "Accountant" of business value.

    Calculates time saved and MDS points for each execution session.
    Compares against task_effort_benchmarks to quantify impact.
    """

    def __init__(self, db_client):
        """
        Initialize MDS Engine.

        Args:
            db_client: Supabase client for database access
        """
        self.db = db_client
        self.benchmarks: Dict[str, TaskBenchmark] = {}
        self._load_benchmarks()
        logger.info("MDSEngine initialized with benchmarks")

    def _load_benchmarks(self):
        """Load task benchmarks from database into RAM"""
        try:
            response = self.db.table("task_effort_benchmarks").select("*").execute()

            for row in response.data:
                benchmark = TaskBenchmark(
                    task_type=row["task_type"],
                    category=row["category"],
                    manual_minutes=row["manual_minutes_est"],
                    ai_minutes=row["ai_minutes_est"],
                    complexity_weight=row["complexity_weight"],
                )
                self.benchmarks[row["task_type"]] = benchmark

            logger.info(f"Loaded {len(self.benchmarks)} task benchmarks")

        except Exception as e:
            logger.error(f"Failed to load benchmarks: {e}")
            # Set defaults
            self._set_default_benchmarks()

    def _set_default_benchmarks(self):
        """Set fallback benchmarks if database unavailable"""
        defaults = [
            ("send_email", "ADMIN", 5.0, 0.1, 1.0),
            ("draft_offer", "HR", 45.0, 2.0, 2.0),
            ("check_hiring_budget", "HR", 10.0, 0.2, 1.0),
            ("fetch_job_template", "HR", 3.0, 0.1, 1.0),
            ("setup_access", "DEV", 15.0, 0.5, 1.5),
            ("approve_payment", "FINANCE", 10.0, 0.3, 2.0),
            ("confirm_offer_acceptance", "HR", 5.0, 0.2, 1.0),
        ]

        for task_type, category, manual, ai, weight in defaults:
            self.benchmarks[task_type] = TaskBenchmark(
                task_type=task_type,
                category=category,
                manual_minutes=manual,
                ai_minutes=ai,
                complexity_weight=weight,
            )

        logger.info(f"Using {len(defaults)} default benchmarks")

    async def calculate_impact(
        self, session_id: str, atoms: List[Any], execution_results: List[Dict[str, Any]]
    ) -> MDSCalculation:
        """
        Calculate MDS for a completed session.

        Args:
            session_id: Unique session identifier
            atoms: List of executed atoms
            execution_results: List of execution results from Phase 4

        Returns:
            MDSCalculation with metrics
        """
        logger.info(f"Calculating MDS for session {session_id}")

        total_minutes_saved = 0.0
        total_mds_points = 0.0
        category_breakdown: Dict[str, float] = {}
        total_tokens = 0
        total_cost = 0.0

        atoms_succeeded = 0
        atoms_failed = 0

        # Build result lookup
        result_map = {r.get("atom_id"): r for r in execution_results}

        for atom in atoms:
            atom_id = getattr(atom, "id", None) or atom.get("id")
            tool_name = getattr(atom, "tool_name", None) or atom.get(
                "tool_name", "unknown"
            )

            # Get execution result
            result = result_map.get(atom_id)
            if not result:
                atoms_failed += 1
                continue

            status = result.get("status", "").upper()
            if status in ["ERROR", "FAILED", "TIMEOUT"]:
                atoms_failed += 1
                continue

            atoms_succeeded += 1

            # Get benchmark for this tool
            benchmark = self.benchmarks.get(tool_name)
            if not benchmark:
                # Unknown tool, use default
                logger.warning(f"No benchmark for tool: {tool_name}, using defaults")
                benchmark = TaskBenchmark(
                    task_type=tool_name,
                    category="GENERAL",
                    manual_minutes=5.0,
                    ai_minutes=0.2,
                    complexity_weight=1.0,
                )

            # Calculate time saved for this atom
            ai_duration_ms = result.get("duration_ms", 100)
            ai_duration_min = ai_duration_ms / 60000.0  # Convert ms to minutes

            time_saved = max(0, benchmark.manual_minutes - ai_duration_min)
            mds_points = time_saved * benchmark.complexity_weight

            total_minutes_saved += time_saved
            total_mds_points += mds_points

            # Category breakdown
            category = benchmark.category
            category_breakdown[category] = (
                category_breakdown.get(category, 0.0) + time_saved
            )

            # Cost tracking (approximate)
            # Estimate tokens from arguments + output length
            args_str = str(result.get("output", ""))
            estimated_tokens = len(args_str) // 4  # Rough approximation
            estimated_cost = estimated_tokens * 0.000002  # GPT-4o-mini pricing

            total_tokens += estimated_tokens
            total_cost += estimated_cost

        result = MDSCalculation(
            session_id=session_id,
            total_minutes_saved=total_minutes_saved,
            total_mds_points=total_mds_points,
            category_breakdown=category_breakdown,
            atoms_executed=len(atoms),
            atoms_succeeded=atoms_succeeded,
            atoms_failed=atoms_failed,
            cost_usd=total_cost,
            tokens_used=total_tokens,
        )

        logger.info(
            f"MDS calculated: {result.total_minutes_saved:.1f} min saved, "
            f"{result.total_mds_points:.1f} points, "
            f"{atoms_succeeded}/{len(atoms)} succeeded"
        )

        return result

    def get_benchmark(self, tool_name: str) -> Optional[TaskBenchmark]:
        """
        Get benchmark for a specific tool.

        Args:
            tool_name: Name of the tool

        Returns:
            TaskBenchmark or None
        """
        return self.benchmarks.get(tool_name)

    def add_benchmark(self, benchmark: TaskBenchmark):
        """
        Add or update a benchmark.

        Args:
            benchmark: TaskBenchmark to add
        """
        self.benchmarks[benchmark.task_type] = benchmark
        logger.info(f"Added benchmark for {benchmark.task_type}")

    async def update_benchmark_db(
        self,
        task_type: str,
        manual_minutes: float,
        ai_minutes: float,
        complexity_weight: float,
        category: str = "GENERAL",
    ):
        """
        Update benchmark in database.

        Args:
            task_type: Tool name
            manual_minutes: Human time estimate
            ai_minutes: AI time estimate
            complexity_weight: Scoring multiplier
            category: Task category
        """
        try:
            # Upsert to database
            self.db.table("task_effort_benchmarks").upsert(
                {
                    "task_type": task_type,
                    "category": category,
                    "manual_minutes_est": manual_minutes,
                    "ai_minutes_est": ai_minutes,
                    "complexity_weight": complexity_weight,
                }
            ).execute()

            # Update RAM cache
            self.benchmarks[task_type] = TaskBenchmark(
                task_type=task_type,
                category=category,
                manual_minutes=manual_minutes,
                ai_minutes=ai_minutes,
                complexity_weight=complexity_weight,
            )

            logger.info(f"Updated benchmark for {task_type}")

        except Exception as e:
            logger.error(f"Failed to update benchmark: {e}")

    async def calibrate_user_benchmark(
        self, org_id: str, task_type: str, user_manual_minutes: float
    ):
        """
        Allow users to customize benchmarks for their organization.

        Per spec: Handle "Fast Human" edge case where user says
        "I can do this in 1 minute, not 5."

        Args:
            org_id: Organization ID
            task_type: Tool name
            user_manual_minutes: User's claimed manual time
        """
        # This would create org-specific benchmarks
        # For now, just update the global benchmark
        benchmark = self.benchmarks.get(task_type)
        if benchmark:
            await self.update_benchmark_db(
                task_type=task_type,
                manual_minutes=user_manual_minutes,
                ai_minutes=benchmark.ai_minutes,
                complexity_weight=benchmark.complexity_weight,
                category=benchmark.category,
            )
            logger.info(
                f"Calibrated {task_type} for org {org_id}: {user_manual_minutes} min"
            )
