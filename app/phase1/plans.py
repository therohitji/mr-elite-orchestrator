# app/phase1/plans.py

from pydantic import BaseModel
from typing import List, Literal, Optional, Dict, Any


class PlanStep(BaseModel):
    """
    Plan step with direct agent/tool references.
    LLM outputs exact names from the injected workspace context.
    """
    step_id: int
    agent: str  # Exact agent name (e.g., "Gmail Agent")
    tool: str   # Exact tool code (e.g., "gmail.send")
    description: str
    parameters: Dict[str, Any] = {}  # Structured tool arguments from LLM
    dependencies: List[int] = []
    
    # Re-planning control fields
    requires_replan: bool = False  # True = decision point, re-plan after execution
    pending_goal: Optional[str] = None  # Goal to achieve after this step (for re-planning)
    
    # Legacy fields (kept for backward compatibility during transition)
    step_name: Optional[str] = None
    capability: Optional[str] = None
    intent: Optional[str] = None
    action_type: Optional[str] = None


class ExecutionPlan(BaseModel):
    reasoning_summary: str
    missing_information: List[str]
    plan: List[PlanStep]
