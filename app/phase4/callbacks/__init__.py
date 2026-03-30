# app/phase4/callbacks/__init__.py
"""
Phase 4 Callbacks: Handlers for complex Phase 4 integrations

These callback functions are used by run_phase4_pipeline() to handle:
1. Clarification requests - Create sessions for user input
2. Re-planning - Invoke CoT planner when atoms require replanning
3. Memory storage - Store execution results to memory layer
"""

from .clarification import create_clarification_handler
from .replan import create_replan_handler
from .memory import create_memory_handler

__all__ = [
    "create_clarification_handler",
    "create_replan_handler",
    "create_memory_handler",
]
