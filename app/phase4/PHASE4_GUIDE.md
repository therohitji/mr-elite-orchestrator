# Phase 4: Execution Layer - Implementation Guide

## Overview

Phase 4 implements the **Execution Layer**, the final stage where atoms interact with the real world. This layer was designed according to the "Central Dispatcher" pattern, which routes atoms to one of three execution "runways" based on their type.

## Architecture

### The Three Runways

```
┌─────────────────────────────────────────┐
│     Dispatcher (Central Switchboard)    │
├─────────────────────────────────────────┤
│                                         │
├─ Runway A: CODE_TOOL (Fast)             │
│  └─ Direct Python functions             │
│  └─ Deterministic, millisecond latency  │
│  └─ Examples: check_budget, stripe_pay  │
│                                         │
├─ Runway B: AI_AGENT (Slow)              │
│  └─ LLM-powered agents                  │
│  └─ Creative, high-latency              │
│  └─ Examples: draft_contract, sentiment │
│                                         │
└─ Runway C: WORKFLOW (Structured)        │
   └─ Pre-defined sequences               │
   └─ LangGraph or Python scripts         │
   └─ Examples: employee_onboarding       │
```

## Core Components

### 1. Dispatcher (`dispatcher.py`)

**Main Execution Engine**

```python
from app.core.execution import execute_atom

# Execute an atom
result = execute_atom(atom)
```

**Features:**
- Registry lookup (finds tool configuration)
- Input sanitization (type safety)
- Timeout protection (30-second hard limit)
- Error handling with cascading failures
- Feedback loop (updates global context)

### 2. Global Context (`context.py`)

**Shared State Management**

```python
from app.core.execution import GlobalContext

# Store output from Atom 1 for Atom 2
context.set("salary_data", {"amount": 50000})

# Retrieve in Atom 2
salary = context.get("salary_data")
```

**Key Methods:**
- `set(key, value)` - Store a value
- `get(key, default)` - Retrieve a value
- `update(dict)` - Batch updates
- `log_execution(atom_id, status, output, error)` - Log execution
- `snapshot()` - Get current state

### 3. Input Sanitizer (`sanitizer.py`)

**Type Safety & Argument Validation**

```python
from app.core.execution.sanitizer import InputSanitizer

# Handles common mistakes
# LLM sends "500 dollars" → sanitizer converts to int 500
schema = {"amount": "int", "email": "str"}
clean_args = InputSanitizer.sanitize_arguments(args, schema)
```

**Supported Types:**
- `int` - Strips currency symbols, converts floats
- `float` - Handles comma separators
- `str` - Strips whitespace
- `bool` - Converts "true"/"false" strings

### 4. Timeout Manager (`timeout.py`)

**Execution Timeout Protection**

```python
from app.core.execution.timeout import timeout_manager

# 30-second hard limit per spec
result = timeout_manager.execute_with_timeout(
    func,
    kwargs={"arg": "value"},
    timeout_seconds=30,
)
```

**Why It Matters:**
- Prevents hanging agents (e.g., Stripe API timeout)
- Protects system stability
- 30 seconds is the maximum per Phase 4 spec

### 5. Dependency Manager (`dependencies.py`)

**Dependency Tracking & Cascading Failures**

```python
from app.core.execution.dependencies import dependency_manager

# Register atom with dependencies
dependency_manager.register_atom("atom_2", ["atom_1"])

# If atom_1 fails, atom_2 is automatically skipped
if atom_1_failed:
    dependency_manager.mark_error("atom_1")
    # This cascades to mark atom_2 as SKIPPED
```

**Workflow:**
1. Atom 1 fails → status = ERROR
2. System finds all atoms depending on Atom 1
3. Marks them as SKIPPED_DUE_TO_FAILURE
4. Does NOT attempt to run them

## Execution Flow

```
1. Dispatcher Receives Atom
   ↓
2. Check Dependencies
   ├─ If dependency failed → SKIP
   └─ If all OK → Continue
   ↓
3. Registry Lookup
   └─ Find tool configuration
   ↓
4. Input Sanitization
   └─ Cast types, validate arguments
   ↓
5. Route to Runway
   ├─ CODE_TOOL: Direct function call (timeout: 30s)
   ├─ AI_AGENT: LLM loop with system prompt (timeout: 30s)
   └─ WORKFLOW: LangGraph invocation (timeout: 30s)
   ↓
6. SUCCESS → Update Global Context
   └─ Save output so next atoms can use it
   ↓
7. Log Execution
   └─ Record status, output, errors
```

## Error Handling

### Three Critical Risks (Per Phase 4 Spec)

#### Risk 1: The "Hanging Agent" (Timeouts)
**Problem:** Tool hangs for 60+ seconds → system freezes
**Solution:** Hard 30-second timeout on all executions

```python
# Wrapped automatically in dispatcher
try:
    result = func(**args)  # 30s max
except TimeoutError:
    return "ERROR: Tool took too long"
```

#### Risk 2: "Hallucinated Argument" (Type Safety)
**Problem:** LLM sends `"500 dollars"` instead of `500` → crash
**Solution:** Input sanitization before execution

```python
# Before execution
args["amount"] = sanitize_int("500 dollars")  # → 500
```

#### Risk 3: Error Propagation (The Crash)
**Problem:** Atom 1 fails → Atom 2 depends on Atom 1 → crash
**Solution:** Dependency cascading

```python
# If Atom 1 fails
if atom_1.status == ERROR:
    # Find all atoms with dependency on atom_1
    # Mark them as SKIPPED (don't run them)
```

## Usage Examples

### Example 1: Execute a Simple CODE_TOOL

```python
from app.core.execution import execute_atom

class Atom:
    id = "atom_1"
    tool_name = "check_budget"
    tool_arguments = {"user_id": "user_1", "amount": 1000.0}
    output_variable = "budget_check"
    dependencies = []

atom = Atom()
result = execute_atom(atom)

# Result:
# {
#   "status": "SUCCESS",
#   "atom_id": "atom_1",
#   "output": {"has_sufficient_funds": True, ...}
# }
```

### Example 2: Execute with Dependencies

```python
class AtomWithDeps:
    id = "atom_2"
    tool_name = "draft_contract"
    tool_arguments = {"contract_type": "service"}
    output_variable = "contract_draft"
    dependencies = ["atom_1"]  # Depends on atom_1

# If atom_1 succeeded, atom_2 will run
# If atom_1 failed, atom_2 will be SKIPPED
result = execute_atom(atom_with_deps)
```

### Example 3: Access Previous Outputs

```python
from app.core.execution import global_context

# Atom 1 saves its output
# Atom 2 retrieves it
salary_data = global_context.get("salary_data")
```

### Example 4: Register a Custom Tool

```python
from app.core.execution import Dispatcher
from app.phase4 import ExecutionRunway, ToolConfig

def my_function(**kwargs):
    return {"result": "success"}

tool = ToolConfig(
    name="my_tool",
    runway_type=ExecutionRunway.CODE_TOOL,
    handler=my_function,
    input_schema={"param": "str"},
)

dispatcher = Dispatcher(tool_registry={
    "my_tool": tool.to_registry_format()
})
```

## Execution Status Enum

```python
from app.phase4 import ExecutionStatus

ExecutionStatus.SUCCESS  # Atom executed successfully
ExecutionStatus.ERROR    # Atom failed
ExecutionStatus.SKIPPED  # Atom skipped due to dependency failure
ExecutionStatus.PENDING  # Atom not yet executed
```

## Execution Orchestrator

**For executing complete plans (multiple atoms):**

```python
from app.phase4.orchestrator import orchestrator

# Execute a list of atoms
atoms = [atom_1, atom_2, atom_3]
summary = orchestrator.execute_plan(atoms)

# Summary includes:
# {
#   "total_atoms": 3,
#   "successful": 2,
#   "failed": 0,
#   "skipped": 1,
#   "duration_seconds": 2.45,
#   "results": [...],
#   "global_context": {...},
# }
```

## Testing & Validation

### Test Registry (See `examples.py`)

Pre-built examples of:
- CODE_TOOLS: `check_budget`, `stripe_pay`, `search_db`
- AI_AGENTS: `draft_contract`, `analyze_sentiment`
- WORKFLOWS: `onboard_employee`

```python
from app.phase4.examples import create_example_registry

registry = create_example_registry()
dispatcher = Dispatcher(tool_registry=registry)
```

## Integration Checklist

- [ ] Import dispatcher from `app.core.execution`
- [ ] Register tools in `TOOL_REGISTRY`
- [ ] Define atom.dependencies for dependent atoms
- [ ] Use `global_context.set()` to share state
- [ ] Handle ExecutionStatus enum in results
- [ ] Test with timeout scenarios
- [ ] Validate input schemas match handler expectations

## Next Steps

1. **Integration with Phase 1-3:**
   - Connect `phase1.tool_registry.TOOL_REGISTRY` to dispatcher
   - Use atoms from phase1.atoms
   - Apply policies from phase2.policy

2. **LLM Integration:**
   - Implement `run_agent_loop()` for AI_AGENT runway
   - Use Gemini 2.5 Flash for creative tasks

3. **Workflow Engine:**
   - Integrate LangGraph for WORKFLOW runway
   - Define sub-graphs for common processes

4. **Monitoring & Observability:**
   - Add metrics for execution times
   - Log all execution attempts
   - Track failure patterns

## Files Structure

```
app/core/execution/
├── __init__.py           # Module exports
├── dispatcher.py         # Main execution engine
├── context.py            # Global state management
├── sanitizer.py          # Input validation
├── timeout.py            # Timeout protection
├── dependencies.py       # Dependency tracking
└── error_handler.py      # Custom exceptions

app/phase4/
├── __init__.py           # Phase 4 models
├── orchestrator.py       # Execution orchestration
├── examples.py           # Reference implementations
└── PHASE4_GUIDE.md       # This document
```

---

**Status:** Phase 4 (Execution) - Complete
**Last Updated:** 2026-01-09
