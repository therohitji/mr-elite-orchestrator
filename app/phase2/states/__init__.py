"""Phase 2: States Layer - Crash-proof workflow state management

This module provides deterministic workflow state management with:
- Session snapshots (current state)
- Append-only event log (full history)
- Lease-based locking (single executor per session)
- Idempotent event processing (retry-safe)

Components:
- StatesRepository: Session snapshot operations
- EventsRepository: Event log operations
- LocksRepository: Lock management
- StateReducer: Deterministic state evolution
- SessionRunner: Main execution loop with lock heartbeat
- ToolWrapper: Idempotent tool execution

Usage:
    from app.phase2.states import create_states_system
    states_sys = create_states_system(db_client)
    session_id = await states_sys.start_session(org_id, workspace_id, user_id, title)
    result = await states_sys.resume_session(session_id)
"""

from app.phase2.states.states_repo import StatesRepository
from app.phase2.states.events_repo import EventsRepository
from app.phase2.states.locks_repo import LocksRepository
from app.phase2.states.reducer import StateReducer
from app.phase2.states.session_runner import SessionRunner
from app.phase2.states.tool_wrapper import ToolWrapper, ExecutionMode

__all__ = [
    "StatesRepository",
    "EventsRepository",
    "LocksRepository",
    "StateReducer",
    "SessionRunner",
    "ToolWrapper",
    "ExecutionMode",
]
