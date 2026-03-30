"""
Phase 5: Evaluation Layer (Analytics + Learning)

This phase provides the "Business Intelligence" and "Learning Loop"
that turns GeniOS from a reactive tool into a self-improving system.

Components:
  1. SessionValidator - Post-mortem validation (plan vs actual)
  2. MDSEngine - Maturity Delta Score calculation (time saved)
  3. AnalyticsStore - Database persistence for metrics
  4. SmartRecombinator - Pattern recognition and recommendations
  5. EvaluationOrchestrator - Main coordinator

Per spec: "The Review Meeting" after execution.
Implements: Analytics tracking, MDS calculation, smart recommendations.
"""

from .validator import SessionValidator, ValidationResult, SessionStatus, FailureType
from .mds_engine import MDSEngine, MDSCalculation, TaskBenchmark
from .analytics_store import AnalyticsStore
from .smart_recommender import SmartRecombinator
from .evaluator import EvaluationOrchestrator, EvaluationResult, create_evaluator

__all__ = [
    # Core classes
    "EvaluationOrchestrator",
    "SessionValidator",
    "MDSEngine",
    "AnalyticsStore",
    "SmartRecombinator",
    # Result types
    "EvaluationResult",
    "ValidationResult",
    "MDSCalculation",
    "TaskBenchmark",
    # Enums
    "SessionStatus",
    "FailureType",
    # Factory
    "create_evaluator",
]
