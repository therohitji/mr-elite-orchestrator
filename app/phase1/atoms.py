from pydantic import BaseModel, Field
from typing import List, Dict, Literal, Any, Optional
from uuid import UUID
from datetime import datetime


class ExecutionAtom(BaseModel):
    """
    Core ExecutionAtom schema per PRD Section 2.1.
    This is the contract between all 5 phases.
    """

    # Identity & Description
    id: UUID = Field(..., description="Unique atom identifier")
    description: str = Field(..., description="Human-readable atom description")

    # Target & Execution
    target_agent: str = Field(
        ...,
        description="Target agent responsible for execution (agent name or category)",
    )

    tool_name: str = Field(
        ..., description="Tool code to execute (validated from Supabase tools table)"
    )
    tool_arguments: Dict[str, Any] = Field(
        default_factory=dict, description="Arguments for tool"
    )

    # Graph Structure
    dependencies: List[UUID] = Field(
        default_factory=list, description="UUIDs of atoms this depends on"
    )

    # Control & Decision Fields
    risk_level: Literal["low", "medium", "high"] = Field(
        ..., description="Risk assessment"
    )
    status: Literal[
        "PENDING",
        "APPROVED",
        "PAUSED",
        "BLOCKED",
        "FAILED",
        "DONE",
    ] = Field(default="PENDING", description="Current execution status")

    tags: List[str] = Field(default_factory=list, description="Metadata tags")
    context_data: Dict[str, Any] = Field(
        default_factory=dict, description="Enrichment from Phase 2"
    )

    # Execution Metadata (Phase 4 & 5)
    execution_id: Optional[UUID] = Field(None, description="Run/execution reference")
    idempotency_key: Optional[str] = Field(None, description="For Phase 4 retry safety")
    created_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow, description="Creation timestamp"
    )
    executed_at: Optional[datetime] = Field(None, description="Execution timestamp")
    completed_at: Optional[datetime] = Field(None, description="Completion timestamp")

    # Execution Results (Phase 5)
    execution_result: Optional[Dict[str, Any]] = Field(
        None, description="Tool output + metadata"
    )
    error: Optional[str] = Field(None, description="Error message if failed")
    cost_usd: Optional[float] = Field(None, description="Estimated/actual cost")
    duration_ms: Optional[float] = Field(None, description="Execution duration")
    
    # Re-planning control fields (carried from PlanStep)
    requires_replan: bool = Field(
        default=False, description="If true, re-invoke planner after execution"
    )
    pending_goal: Optional[str] = Field(
        None, description="Goal for re-planning after this atom completes"
    )
    original_user_goal: Optional[str] = Field(
        None, description="Original user input for context in re-planning"
    )
