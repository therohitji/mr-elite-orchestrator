"""Phase 2: Enrichment & States Layer

This phase consists of two critical components:

1. ENRICHMENT LAYER (Logic + Context + Resources)
   - Parallel execution of policy checking and context retrieval
   - Atom validation against business rules
   - Resource injection (templates, files, data)
   - Human-in-the-loop escalation

2. STATES LAYER (Crash-proof workflow state management)
   - Deterministic session snapshots
   - Append-only event log
   - Lease-based distributed locking
   - Crash recovery with event replay
   - Idempotent tool execution

Both layers work together to enable resilient, auditable workflow execution.
"""

from app.phase2.states import (
    StatesRepository,
    EventsRepository,
    LocksRepository,
    StateReducer,
    SessionRunner,
    ToolWrapper,
    ExecutionMode,
)

__all__ = [
    "StatesRepository",
    "EventsRepository",
    "LocksRepository",
    "StateReducer",
    "SessionRunner",
    "ToolWrapper",
    "ExecutionMode",
]
