# app/routers/shared.py
"""
Shared Schemas and Utilities for Phase Routers

This module consolidates common schemas and utility functions used across
Phase 1, 2, 3, and 4 routers to avoid code duplication.
"""

from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel


# ═══════════════════════════════════════════════════════════════
# SHARED SCHEMAS
# ═══════════════════════════════════════════════════════════════

class AtomInput(BaseModel):
    """
    Simplified atom input for testing Phase routers.
    
    This is a superset of all fields needed by Phase 2, 3, and 4.
    Each router only uses the fields it needs.
    """
    id: Optional[str] = None
    tool_name: str
    target_agent: str
    description: str
    risk_level: str = "LOW"
    tool_arguments: Dict[str, Any] = {}
    context_data: Dict[str, Any] = {}
    status: str = "READY"  # READY, APPROVED, etc.
    dependencies: List[str] = []  # For Phase 4 dependency tracking


# ═══════════════════════════════════════════════════════════════
# SHARED UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def serialize_uuid(obj):
    """
    Convert UUIDs to strings recursively.
    
    This enables JSON serialization of objects containing UUID fields.
    Works on dicts, lists, and nested structures.
    
    Args:
        obj: Any object that may contain UUIDs
        
    Returns:
        Same structure with UUIDs converted to strings
    """
    if isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: serialize_uuid(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_uuid(item) for item in obj]
    return obj


def atom_input_to_execution_atom(atom_input: AtomInput):
    """
    Convert AtomInput (Pydantic model) to ExecutionAtom (dataclass).
    
    Used for testing Phase routers with simplified input format.
    
    Args:
        atom_input: AtomInput from API request
        
    Returns:
        ExecutionAtom instance suitable for phase processing
    """
    from app.phase1.atoms import ExecutionAtom
    from uuid import uuid4
    
    return ExecutionAtom(
        id=UUID(atom_input.id) if atom_input.id else uuid4(),
        tool_name=atom_input.tool_name,
        target_agent=atom_input.target_agent,
        description=atom_input.description,
        risk_level=atom_input.risk_level,
        tool_arguments=atom_input.tool_arguments,
        context_data=atom_input.context_data,
        status=atom_input.status,
        dependencies=[UUID(d) if isinstance(d, str) else d for d in atom_input.dependencies],
    )
