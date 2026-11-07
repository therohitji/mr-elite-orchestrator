<!-- presentation:start -->
<p align="center"><img src="https://raw.githubusercontent.com/therohitji/therohitji/main/repos/mr-elite-orchestrator.svg" width="100%" alt="Mr. Elite orchestrator: a 5 phase engine that turns a plain language request into policy checked, approved and logged actions"></p>

Mr. Elite is the engine behind an AI employee. A person asks for something in plain language, for example "set up a call with the candidate and email the offer", and the engine turns it into small steps, checks each step against company rules, asks a human where it should, runs the rest and then measures how much time it saved. It is built for teams that want an assistant to act in Gmail and Calendar without being left unsupervised. 5 startups piloted the AI employee and 1 company piloted the Admin AI employee. Senior AI engineers at IBM, Adobe and Google reviewed it.

## The problem

Agents could already call Gmail or Calendar. Nobody had taught them to think in order, and nobody could trust them to act for a company. The gaps were specific:

- "Onboard the new hire" is really 6 to 10 steps with dependencies. A single model call does not keep them in order.
- Companies have written rules, such as "offers above a limit need approval". A rule applies to one step, not to the whole prompt.
- Emails, phone numbers and keys end up in prompts, and a mail body can carry instructions meant to hijack the agent.
- Tools fail. Without timeouts, retries and a rule that a failed step blocks the steps that depend on it, one bad call leaves a half finished job.
- Nobody could say what the assistant saved, because nothing compared manual time with run time.

The engine puts one named component against each gap.

## How I approached it

The rule written into the code and docs is: decide first, validate, control and secure, execute, then evaluate and learn. Each phase reads and enriches the same typed object, so nothing is passed around as loose text.

```mermaid
flowchart LR
  A["Prompt plus workspace and user ids"] --> B["Phase 1 Planning"]
  B -->|"CHAT or QUERY"| X["Early exit with an answer"]
  B -->|"TASK or GOAL"| C["Phase 2 Reasoning"]
  C --> D["Phase 3 Control"]
  D -->|"PAUSED"| H["Human approval"]
  H --> D
  D -->|"BLOCKED"| R["Replan or stop"]
  D -->|"APPROVED"| E["Phase 4 Execution"]
  E --> F["Phase 5 Evaluation"]
  F -.->|"learned preferences"| B
```

**Key decisions**

| Decision | Why | Trade-off |
|---|---|---|
| One typed `ExecutionAtom` passed through all 5 phases | Every phase can add context without breaking the next one (`app/phase1/atoms.py`) | Every change to the contract touches all phases |
| Rules checked per step, not per prompt | "Send an email" can be fine while "send it to an outside domain" is not | More checks per request |
| A forbidden request is rejected before any model call | Cheaper and safer than asking a model to refuse (`app/phase1/pre_flight_check.py`) | The rule list has to be kept current |
| Gates are fixed code, not model opinion | Privacy, security, rate limits and approval give the same answer every time (`app/core/control/`) | Less flexible than a judge model |
| A failed step blocks its dependents, independent steps still run | Avoids half finished jobs without stopping everything | Needs the dependency graph to be right |
| Time saved is scored against stored manual benchmarks | Gives the team a number to argue about (`app/phase5/mds_engine.py`) | The benchmarks are estimates that someone must maintain |

## What I built

| Part | What it does | Where |
|---|---|---|
| Intent classifier | Labels a prompt `CHAT`, `QUERY`, `TASK` or `GOAL`, with complexity, domain and risk | `app/phase1/classifier.py` |
| Planner and atomizer | Breaks a goal into steps with ids, dependencies and risk tags | `app/phase1/cot_planner.py`, `atomizer.py` |
| Policy engine | Matches company rules to each step and returns a traffic light | `app/phase2/policy/` |
| Memory and state checks | Recalls past runs and checks budget and role access | `app/phase2/memory/`, `app/phase2/checks/` |
| Control gates | Redacts personal data, strips injection text, rate limits, queues approvals | `app/core/control/` |
| Dispatcher | Sends each approved step to a tool, an agent or a workflow, in parallel where possible | `app/phase4/dispatcher.py` |
| Retry and timeout | 3 retries with backoff, a 30 second limit per call | `app/phase4/retry_engine.py`, `app/core/execution/timeout.py` |
| Gmail and Calendar tools | Read, draft and send mail; list, create, update and delete events | `app/tools/` |
| Email and calendar agents | Two LangGraph graphs | `app/agents/email/`, `app/agents/calendar/` |
| Evaluation | Labels the run, scores time saved, turns user edits into preference rules | `app/phase5/` |
| Memory ingestion | Loads documents into the vector store | `app/ingestion/` |

## How it works

1. **Planning** (`app/phase1/`). A pre-flight check rejects forbidden requests. A classifier labels the prompt. Goals go to a Chain of Thought planner, and the atomizer turns the plan into a list of atoms.
2. **Reasoning** (`app/phase2/`). Two threads run together: one checks policy and resources, the other recalls memory and checks state. Results are merged into each atom.
3. **Control** (`app/phase3/`, `app/core/control/`). Privacy, security, reliability and approval gates run in order. Each atom comes out `APPROVED`, `BLOCKED` or `PAUSED`.
4. **Execution** (`app/phase4/`). Approved atoms run in parallel where they are independent. Outputs flow into dependent steps.
5. **Evaluation** (`app/phase5/`). In the background the run is labelled `COMPLETED`, `PARTIAL`, `BLOCKED` or `FAILED`, time saved is scored, and edits become preferences for next time.

```mermaid
stateDiagram-v2
  [*] --> PENDING
  PENDING --> APPROVED: "passes Phase 2 and 3"
  PENDING --> PAUSED: "soft violation on high risk"
  PENDING --> BLOCKED: "hard violation"
  PAUSED --> APPROVED: "human approves"
  APPROVED --> DONE: "tool succeeds"
  APPROVED --> FAILED: "retries exhausted"
  DONE --> [*]
  FAILED --> [*]
  BLOCKED --> [*]
```

## What makes it different

<p align="center"><img src="https://raw.githubusercontent.com/therohitji/therohitji/main/repos/mr-elite-orchestrator/where-it-sits.svg" width="100%" alt="Venn diagram: Mr. Elite sits where planning, safety control and learning meet"></p>

| Capability | A chatbot | A script of API calls | A person doing it by hand | Mr. Elite |
|---|---|---|---|---|
| Breaks a vague goal into ordered steps | One reply, no step graph | Fixed when written | The person decides | Planner and atomizer build a dependency graph |
| Checks company rules per step | No | Hard coded if statements | Memory and habit | Rules matched and judged per step |
| Redacts personal data, blocks injection | No | Rarely | Trust | Guards run before execution |
| Pauses for a human on risky steps | No | No | Always, for everything | Only paused and high risk steps wait |
| Retries and timeouts | No | Written by hand per call | The person retries | Backoff, 3 retries, 30 second limit |
| Audit trail | Chat log | Application logs | Email trail | Execution, security and policy records in SQL |
| Measures time saved | No | No | Not measured | Scored per task against manual benchmarks |

## By the numbers

<p align="center"><img src="https://raw.githubusercontent.com/therohitji/therohitji/main/repos/mr-elite-orchestrator/code-by-area.svg" width="100%" alt="Bar chart of Python lines per area, with core and Reasoning the largest"></p>

The shared infrastructure and the Reasoning phase hold the most code, which fits a system whose hard part is deciding, not calling.

<p align="center"><img src="https://raw.githubusercontent.com/therohitji/therohitji/main/repos/mr-elite-orchestrator/endpoints-by-router.svg" width="100%" alt="Bar chart of endpoints per router: phase 3 has 7, phase 1 has 6, the rest have 4"></p>

Phase 3 exposes the most endpoints because privacy, security and approvals can each be tested on their own.

<p align="center"><img src="https://raw.githubusercontent.com/therohitji/therohitji/main/repos/mr-elite-orchestrator/tables-by-migration.svg" width="100%" alt="Column chart of tables created per migration file"></p>

Evaluation adds the most tables, because analytics, benchmarks, feedback and recommendations each need their own record.

## What this shows

- The problem is stated as five concrete gaps, and each gap has a named component (`app/phase3/`, `app/phase5/`).
- Safety is code that gives the same answer every time, not a model's opinion. Privacy, injection, rate and approval gates sit in `app/core/control/`.
- Failure is planned for: 3 retries, a 30 second limit, and a failed step that stops only its own dependents.
- The system keeps score. It measures time saved and learns from user edits, so its value can be argued with numbers.

## What I would do next

- Add automated tests. The repo has none, only a Postman collection with 5 requests.
- Split `app/main.py` (1,870 lines) into smaller modules.
- Move the dispatcher's agent registry into configuration instead of code.
- Back the manual time benchmarks with measured pilot data.

## Tech stack

| Technology | What it does here |
|---|---|
| Python, FastAPI | HTTP layer and async request handling |
| Pydantic | `ExecutionAtom`, plan and result contracts |
| LangGraph | Email and calendar agent graphs |
| Google Gemini | Classification, planning, judging and embeddings |
| Supabase, pgvector | Policies, memory, state, logs and vector search (768 dimensions) |
| Redis | Rate limit counters |
| MongoDB | OAuth tokens for Gmail and Calendar |
| Google API client | Gmail and Calendar calls |
| NetworkX | Dependency graph in the atomizer |

## Run it locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Apply `migration.sql` and then the `PHASE2` to `PHASE5` SQL files to your Supabase project. Set the environment variables listed in `README.md` for Gemini, Supabase, Redis and MongoDB. Import `Mr_Elite_Postman_Collection.json` to call the test routes.

Built by [Rohit Swerashi](https://github.com/therohitji), Founder & CEO of [GeniOS](https://thegenios.com). [Book a call](https://cal.com/rohitswerashi/genios).

<details>
<summary>Full technical documentation</summary>

# MR ELITE, Autonomous Orchestration Engine

> **Decision First → Validate → Control/Secure → Execute → Evaluate/Learn**

MR ELITE (codename **GeniOS**) is a 5-phase autonomous orchestration engine that converts raw, vague user intent into safe, atomic, policy-compliant actions. It is **not a chatbot**, it is a closed-loop execution runtime with deterministic gates, strict contracts, human escalation, and a learning loop.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [5-Phase Pipeline](#5-phase-pipeline)
  - [Phase 1: Planning](#phase-1-planning)
  - [Phase 2: Reasoning](#phase-2-reasoning)
  - [Phase 3: Control](#phase-3-control)
  - [Phase 4: Execution](#phase-4-execution)
  - [Phase 5: Evaluation](#phase-5-evaluation)
- [Core Data Contract: ExecutionAtom](#core-data-contract-executionatom)
- [Project Structure](#project-structure)
- [Core Modules](#core-modules)
- [Agents](#agents)
- [Tools](#tools)
- [API Endpoints](#api-endpoints)
- [Database Schema](#database-schema)
- [Environment Variables](#environment-variables)
- [Tech Stack](#tech-stack)
- [Setup & Installation](#setup--installation)
- [End-to-End Data Flow Example](#end-to-end-data-flow-example)
- [Key Design Patterns](#key-design-patterns)
- [Ingestion Pipeline](#ingestion-pipeline)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         RAW USER PROMPT                                  │
│           + workspace_id + org_id + user_id + chat history               │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PHASE 1: PLANNING                                                       │
│  Pre-Flight → Heuristic Classifier → Semantic Classifier → CoT Planner  │
│  → Atomizer                                                              │
│  Output: DAG of ExecutionAtoms with UUIDs, dependencies, risk tags       │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PHASE 2: REASONING (Parallel Enrichment)                                │
│  ┌─────────────────────────┐  ┌─────────────────────────┐               │
│  │ Thread A: LOGIC         │  │ Thread B: CONTEXT       │               │
│  │ • Policy Engine         │  │ • Memory Engine         │               │
│  │ • Resource Engine       │  │ • State Checks          │               │
│  └─────────────────────────┘  └─────────────────────────┘               │
│  Output: Atoms enriched with policies, resources, memory, state          │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PHASE 3: CONTROL (Sequential Middleware)                                │
│  Privacy Guard → Security Guard → Reliability Guard → Approval Gate      │
│  Output: Atoms marked APPROVED / BLOCKED / PAUSED                        │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PHASE 4: EXECUTION                                                      │
│  Central Dispatcher routes each atom:                                    │
│    CODE_TOOL  → Direct Python function call                              │
│    AI_AGENT   → Spawns sub-agent session (LangGraph)                     │
│    WORKFLOW   → Invokes LangGraph subgraph                               │
│  Output: Structured results + error logs per atom                        │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PHASE 5: EVALUATION (Background)                                        │
│  Validator → MDS Calculator → Analytics Store → Smart Recommender        │
│  Output: Metrics + MDS score + learned preference rules                  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 5-Phase Pipeline

### Phase 1: Planning

**Location:** `app/phase1/` | **Router:** `app/routers/phase1.py`

Converts a raw user prompt into a structured DAG of `ExecutionAtom` objects.

| Layer | Component | File | Purpose |
|-------|-----------|------|---------|
| 0 | Pre-Flight Check | `pre_flight_check.py` | Ultra-fast vector similarity search against blocking policies. Rejects forbidden requests before any LLM call. |
| 1 | Heuristic Classifier | `classifier.py` | Regex-based fast-path for greetings, abort commands, system commands. Returns `"chat"`, `"interrupt"`, `"system"`, `"error"`, or `None` (escalate). |
| 2 | Semantic Classifier | `classifier.py` | LLM-based classification into `CHAT`, `QUERY`, `TASK`, or `GOAL`. Outputs intent, complexity (1-10), domain, risk level, reasoning. |
| 3 | CoT Planner | `cot_planner.py` | Chain-of-Thought reasoning for `GOAL`-type requests. Loads available agents/tools from workspace cache. Produces `ExecutionPlan` with `PlanStep` objects. Supports **re-planning** from decision points. |
| 4 | Atomizer | `atomizer.py` | Converts `ExecutionPlan` → `List[ExecutionAtom]`. Validates agent/tool names against workspace. Creates UUIDs, dependencies, risk tags. |

**Supporting files:**
- `atoms.py`, `ExecutionAtom` schema (the primary contract for all phases)
- `plans.py`, `PlanStep` and `ExecutionPlan` schemas
- `workspace_cache.py`, Caches available agents/tools from Supabase
- `config.py`, Phase 1 configuration constants
- `classifier_prompt.py`, LLM prompt template for semantic classification
- `cot_prompt.py`, LLM prompt template for Chain-of-Thought planning
- `param_extractor.py`, Parameter extraction utilities
- `semantic_router.py`, Routes requests by intent type

**Output:** `Phase1Result`
```python
@dataclass
class Phase1Result:
    success: bool
    phase_reached: str       # "pre_flight" | "heuristic" | "semantic" | "planning" | "atomization"
    early_exit: bool         # True for CHAT/QUERY intents (no execution needed)
    early_exit_response: Optional[Dict]
    heuristic_result: Optional[str]
    classification: Optional[ClassificationOutput]
    plan: Optional[ExecutionPlan]
    atoms: Optional[List[ExecutionAtom]]
    atomization_debug: Optional[Dict]
    error: Optional[str]
```

**Early exits:**
- `CHAT` → Generate conversational response, skip remaining phases
- `QUERY` → RAG retrieval, return answer, skip remaining phases
- `TASK` → Single atom, skip CoT planner
- `GOAL` → Full pipeline (CoT → Atomize → Phase 2+)

---

### Phase 2: Reasoning

**Location:** `app/phase2/` | **Router:** `app/routers/phase2.py`

Enriches each atom with real-world context using **parallel** evaluation threads.

```
                    ┌──────────────────────────────┐
                    │   Enriched Atoms (Input)      │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────┴───────────────┐
                    │     asyncio.gather            │
         ┌──────────┴──────────┐  ┌────────────────┴───────────┐
         │  Thread A: LOGIC    │  │  Thread B: CONTEXT         │
         │  ┌────────────────┐ │  │  ┌──────────────────────┐  │
         │  │ Policy Engine  │ │  │  │ Memory Engine        │  │
         │  │ (vector match  │ │  │  │ (episodic+reflective │  │
         │  │  → judge)      │ │  │  │  recall)             │  │
         │  └────────────────┘ │  │  └──────────────────────┘  │
         │  ┌────────────────┐ │  │  ┌──────────────────────┐  │
         │  │ Resource Engine│ │  │  │ State Checks         │  │
         │  │ (templates,    │ │  │  │ (budget, permissions,│  │
         │  │  configs)      │ │  │  │  role access)        │  │
         │  └────────────────┘ │  │  └──────────────────────┘  │
         └─────────────────────┘  └────────────────────────────┘
                    │                          │
                    └──────────┬───────────────┘
                               │
                    ┌──────────▼───────────────┐
                    │   Merge → atom.context_data  │
                    └──────────────────────────┘
```

**Policy Engine Decision Matrix (Traffic Lights):**

| Severity | Risk Level | Decision | Action |
|----------|-----------|----------|--------|
| Hard violation | Any | **RED** | BLOCK/FAIL + trigger replanning |
| Soft violation | HIGH | **YELLOW** | PAUSE + request approval |
| Soft violation | LOW/MEDIUM | **GREEN (flagged)** | Proceed with warning |
| No violation | Any | **GREEN** | Proceed |

**Memory Engine** returns episodic + reflective memories as **informational context only** (generates warnings, never blocks).

**State Checks** are deterministic SQL lookups for binary facts (budget open? role permissions? calendar access?). Hard blocks from state override policy decisions.

**Output:** `Phase2Result`
```python
@dataclass
class Phase2Result:
    success: bool
    enriched_atoms: List[ExecutionAtom]
    evaluation_results: List[Dict]
    clarification_atoms: List[ExecutionAtom]
    ready_count: int
    requires_approval_count: int
    blocked_count: int
    clarification_count: int
    error: Optional[str]
```

---

### Phase 3: Control

**Location:** `app/phase3/` | **Router:** `app/routers/phase3.py`

Final checkpoint before real-world execution. Sequential middleware pipeline:

| Guard | Purpose | Action on Violation |
|-------|---------|---------------------|
| **Privacy Guard** | Regex scan for PII (emails, phone, SSN, API keys, secrets) | Redact content OR block atom |
| **Security Guard** | Heuristic detection for prompt injection in tool-fed content | Strip hostile segments OR force read-only summarization |
| **Reliability Guard** | Redis rate-limit counters + rough token/cost estimation | Throttle, queue, or request user confirmation |
| **Approval Gate** | Final human checkpoint for `PAUSED` atoms or `HIGH` risk tools | Hold execution until explicit approval (Yes/No in UI) |

**Supporting components:**
- `preflight.py`, Ultra-fast vector search for BLOCK policies
- `agent_compatibility_validator.py`, Validates agent assignments against capability registry
- `app/core/control/approval_service.py`, Manages the approval queue
- `app/core/control/audit_logger.py`, Immutable security event log

**Output:** `Phase3Result`
```python
@dataclass
class Phase3Result:
    success: bool
    approved_atoms: List[ExecutionAtom]
    blocked_atoms: List[ExecutionAtom]
    pending_approval_atoms: List[ExecutionAtom]
    validation_results: List[Dict]
    passed_count: int
    blocked_count: int
    approval_required_count: int
    privacy_failures: int
    security_failures: int
    reliability_failures: int
    error: Optional[str]
```

---

### Phase 4: Execution

**Location:** `app/phase4/` | **Router:** `app/routers/phase4.py`

Executes **only approved atoms** via deterministic routing.

**Central Dispatcher** (`dispatcher.py`) routes atoms by runway type:

| Runway | Target | Example |
|--------|--------|---------|
| `CODE_TOOL` | Direct Python function call | `list_threads`, `list_events` |
| `AI_AGENT` | Spawn sub-agent session with LangGraph | `send_email` (needs content generation) |
| `WORKFLOW` | Multi-step LangGraph subgraph | Complex multi-tool chains |

**Agent Handler Registry:**
```python
AGENT_HANDLERS = {
    "email agent":          "email_agent",
    "calendar agent":       "calendar_agent",
    "documentation agent":  "documentation_agent",
    "data extraction agent":"data_extractor_agent",
    "writing agent":        "writing_agent",
    "summariser agent":     "summarizer_agent",
    "system":               None,
}
```

**Safety mechanisms:**
- **Hard timeouts:** 30 seconds per tool call
- **Type enforcement:** Cast/coerce `tool_arguments` to schema before calling
- **Idempotency:** `atom.id` as idempotency key; tools support safe retry
- **Retry strategy:** Exponential backoff (`base × 2^retry_count`), max 3 retries, max backoff 3600s
- **Partial failure handling:** Failed atoms block dependents; independent atoms run in parallel
- **Cancellation:** Mark `run_id` cancelled, kill queued atoms
- **Immutable audit log:** Input, output, tool, timestamps, decision path

**Key components:**
- `orchestrator.py`, Manages atom sequencing, dependency topological sort, parallel execution
- `retry_engine.py`, Exponential backoff with configurable limits
- `result_store.py`, Persists execution results to database
- `response_formatter.py`, Formats results for user-facing response
- `workflow_manager.py`, Manages multi-step workflow execution
- `callbacks/`, Clarification, memory, and replan callbacks during execution

**Output:** `Phase4Result`
```python
@dataclass
class Phase4Result:
    success: bool
    executed_atoms: List[ExecutionAtom]
    clarification_atoms: List[ExecutionAtom]
    replan_atoms: List[ExecutionAtom]
    dispatcher_results: List[DispatcherResult]
    execution_results: List[Dict]
    atom_outputs: Dict[str, Any]       # For dependency chaining between atoms
    formatted_response: Optional[Dict]
    success_count: int
    failed_count: int
    timeout_count: int
    total_duration_ms: float
    error: Optional[str]
```

---

### Phase 5: Evaluation

**Location:** `app/phase5/`

Runs **asynchronously in the background** after execution completes.

| Component | File | Purpose |
|-----------|------|---------|
| **Session Validator** | `validator.py` | Validates execution results against schema. Returns status: `COMPLETED`, `PARTIAL`, `BLOCKED`, `FAILED`. |
| **MDS Engine** | `mds_engine.py` | Calculates **Maturity Delta Score**, quantifies time saved. Formula: `MDS = Σ(T_manual - T_execution) × W_complexity`. References `task_effort_benchmarks` table. |
| **Analytics Store** | `analytics_store.py` | Logs metrics to `user_analytics_ledger`. Stores session summaries in `execution_sessions`. |
| **Smart Recommender** | `smart_recommender.py` | Implicit feedback loop: detects user edits → extracts preference rules → stores in vector DB. Next runs retrieve learned rules for personalization. |
| **Evaluation Orchestrator** | `evaluator.py` | Coordinates the full evaluation pipeline: validate → MDS → analytics → learning. |

**MDS Reference Benchmarks (examples):**

| Task Type | Manual (min) | AI (min) | Complexity Weight |
|-----------|-------------|----------|-------------------|
| `send_email` | 5 | 0.1 | 1.0 |
| `draft_offer` | 45 | 2 | 2.0 |
| `check_hiring_budget` | 10 | 0.2 | 1.0 |
| `setup_access` | 15 | 0.5 | 1.5 |

---

## Core Data Contract: ExecutionAtom

The `ExecutionAtom` is the **primary contract** flowing through all 5 phases. Every phase reads, enriches, or acts on atoms.

**Location:** `app/phase1/atoms.py`

```python
class ExecutionAtom(BaseModel):
    # ─── Identity ───
    id: UUID                                    # Unique atom identifier
    description: str                            # Human-readable description

    # ─── Target & Execution ───
    target_agent: str                           # Agent responsible (e.g., "email agent")
    tool_name: str                              # Tool code (validated from Supabase)
    tool_arguments: Dict[str, Any]              # Arguments for tool call

    # ─── Graph Structure ───
    dependencies: List[UUID]                    # UUIDs this atom depends on

    # ─── Control Fields ───
    risk_level: Literal["low", "medium", "high"]
    status: Literal["PENDING", "APPROVED", "PAUSED", "BLOCKED", "FAILED", "DONE"]

    # ─── Enrichment (Phase 2) ───
    tags: List[str]
    context_data: Dict[str, Any]               # Policy, resource, memory, state

    # ─── Execution Metadata (Phase 4 & 5) ───
    execution_id: Optional[UUID]
    idempotency_key: Optional[str]
    execution_result: Optional[Dict]
    error: Optional[str]
    cost_usd: Optional[float]
    duration_ms: Optional[float]
```

**Atom lifecycle:**

```
PENDING → (Phase 2 enrichment) → APPROVED | PAUSED | BLOCKED
                                       │          │
                                       ▼          ▼
                                  (Phase 4)   (Await approval)
                                       │          │
                                       ▼          ▼
                                   DONE | FAILED  APPROVED → Execute
```

---

## Project Structure

```
mr-elite-orchestrator/
├── app/
│   ├── main.py                        # FastAPI app entry point, wires all 5 phases
│   ├── main_orchestrator.py           # High-level orchestration logic
│   ├── schemas.py                     # Shared Pydantic schemas
│   │
│   ├── phase1/                        # PLANNING
│   │   ├── atoms.py                   # ExecutionAtom contract
│   │   ├── plans.py                   # PlanStep, ExecutionPlan schemas
│   │   ├── pre_flight_check.py        # Layer 0: blocking policy vector search
│   │   ├── classifier.py              # Heuristic + Semantic classifier
│   │   ├── classifier_prompt.py       # LLM prompt for classification
│   │   ├── cot_planner.py            # Chain-of-Thought planner
│   │   ├── cot_prompt.py             # LLM prompt for CoT planning
│   │   ├── atomizer.py               # Plan → Atoms conversion
│   │   ├── param_extractor.py        # Parameter extraction utilities
│   │   ├── semantic_router.py        # Intent-based routing
│   │   ├── workspace_cache.py        # Agent/tool cache from Supabase
│   │   └── config.py                 # Phase 1 constants
│   │
│   ├── phase2/                        # REASONING
│   │   ├── evaluator.py              # Main orchestrator (parallel threads)
│   │   ├── contracts.py              # Phase 2 data contracts
│   │   ├── results.py                # Result types
│   │   ├── memory/
│   │   │   ├── memory_engine.py      # Episodic + reflective memory
│   │   │   ├── memory_manager.py     # Memory lifecycle management
│   │   │   └── retrieval.py          # Hybrid semantic search + context injection
│   │   ├── memory_engine.py          # Legacy memory engine
│   │   ├── policy/
│   │   │   ├── policy_engine.py      # Vector match → judge → traffic light
│   │   │   └── policy_store.py       # Policy persistence
│   │   ├── resources/                 # Resource fetching engine
│   │   ├── states/                    # State management
│   │   │   ├── states_repo.py        # State persistence
│   │   │   ├── events_repo.py        # Event sourcing
│   │   │   ├── locks_repo.py         # Distributed locks
│   │   │   ├── reducer.py            # State reducer
│   │   │   ├── session_runner.py     # Session state machine
│   │   │   └── tool_wrapper.py       # State-aware tool wrapper
│   │   └── checks/
│   │       ├── state_check.py        # Deterministic state validation
│   │       └── resource_check.py     # Resource availability check
│   │
│   ├── phase3/                        # CONTROL
│   │   ├── preflight.py              # Fast blocking policy check
│   │   └── agent_compatibility_validator.py  # Agent capability validation
│   │
│   ├── phase4/                        # EXECUTION
│   │   ├── dispatcher.py             # Central dispatcher (CODE_TOOL/AI_AGENT/WORKFLOW)
│   │   ├── orchestrator.py           # Atom sequencing + parallel execution
│   │   ├── retry_engine.py           # Exponential backoff retry
│   │   ├── result_store.py           # Execution result persistence
│   │   ├── response_formatter.py     # User-facing response builder
│   │   ├── workflow_manager.py       # Multi-step workflow execution
│   │   ├── output_schemas.py         # Output type definitions
│   │   └── callbacks/
│   │       ├── clarification.py      # Mid-execution clarification
│   │       ├── memory.py             # Memory callbacks
│   │       └── replan.py             # Re-planning triggers
│   │
│   ├── phase5/                        # EVALUATION
│   │   ├── evaluator.py              # Evaluation orchestrator
│   │   ├── validator.py              # Session result validator
│   │   ├── mds_engine.py             # Maturity Delta Score calculator
│   │   ├── analytics_store.py        # Metrics persistence
│   │   └── smart_recommender.py      # Implicit feedback → preference rules
│   │
│   ├── core/                          # SHARED INFRASTRUCTURE
│   │   ├── config.py                 # Environment variable management
│   │   ├── gemini_client.py          # Google Gemini API (LLM + embeddings)
│   │   ├── supabase_client.py        # Supabase PostgreSQL + pgvector client
│   │   ├── tool_factory.py           # Tool instantiation (Gmail, Calendar)
│   │   ├── tool_tokens.py            # OAuth token retrieval from MongoDB
│   │   ├── clarification_manager.py  # Multi-turn clarification flows
│   │   ├── knowledge_retriever.py    # RAG context retrieval
│   │   ├── thread_history.py         # Conversation thread tracking
│   │   ├── session/
│   │   │   ├── session_service.py    # Session lifecycle (7-day TTL)
│   │   │   └── state_schema.py       # SessionState, SessionStatus enums
│   │   ├── control/                   # Control plane infrastructure
│   │   │   ├── engine.py             # Control engine orchestrator
│   │   │   ├── models.py             # Control data models
│   │   │   ├── approval_service.py   # Approval queue management
│   │   │   ├── approval_store.py     # Approval persistence
│   │   │   ├── approval_models.py    # Approval data models
│   │   │   ├── audit_logger.py       # Immutable event log
│   │   │   ├── policy_judge.py       # Deterministic policy evaluation
│   │   │   ├── policy_models.py      # Policy data models
│   │   │   ├── policy_retriever.py   # Vector policy retrieval
│   │   │   ├── policy_store.py       # Policy persistence
│   │   │   ├── execution_gate.py     # Go/no-go gate logic
│   │   │   ├── patterns.py           # Security detection patterns
│   │   │   ├── redis_client.py       # Redis connection
│   │   │   ├── safe_redis.py         # Safe Redis wrapper (graceful fallback)
│   │   │   ├── resource_config.py    # Resource limit configuration
│   │   │   ├── resume_service.py     # Workflow resumption after approval
│   │   │   ├── segment1_sanitizer.py # PII/content sanitization
│   │   │   ├── segment2_policy.py    # Policy middleware
│   │   │   ├── segment3_resource.py  # Resource limit middleware
│   │   │   └── segment4_approval.py  # Approval middleware
│   │   └── execution/                 # Execution infrastructure
│   │       ├── context.py            # Global execution context
│   │       ├── dependencies.py       # DAG dependency management
│   │       ├── error_handler.py      # Centralized error handling
│   │       ├── sanitizer.py          # Input sanitization
│   │       └── timeout.py            # Hard timeout enforcement (30s)
│   │
│   ├── agents/                        # DOMAIN AGENTS
│   │   ├── base.py                   # Abstract base agent + SkillCategory + AgentSkillOutput
│   │   ├── agent_registry.py         # Capability registry for validation
│   │   ├── email_agent.py            # Email agent (send, draft, list, get)
│   │   ├── email/
│   │   │   ├── graph.py              # LangGraph email workflow
│   │   │   ├── service.py            # Email business logic
│   │   │   ├── models.py             # Email data models
│   │   │   └── state.py              # Email agent state schema
│   │   ├── calendar_agent.py         # Calendar agent (create, list, update, delete)
│   │   └── calendar/
│   │       ├── graph.py              # LangGraph calendar workflow
│   │       ├── service.py            # Calendar business logic
│   │       ├── models.py             # Calendar data models
│   │       └── state.py              # Calendar agent state schema
│   │
│   ├── microagents/                   # LIGHTWEIGHT AGENTS
│   │   └── summarizer/
│   │       ├── summarizer_agent.py   # Text + email summarization
│   │       └── models.py             # Summarizer data models
│   │
│   ├── tools/                         # RAW TOOL IMPLEMENTATIONS
│   │   ├── gmail.py                  # GmailTool (Google Gmail API v1)
│   │   └── calendar.py               # CalendarTool (Google Calendar API v3)
│   │
│   ├── routers/                       # FASTAPI ROUTERS
│   │   ├── phase1.py                 # /phase1/* endpoints
│   │   ├── phase2.py                 # /phase2/* endpoints
│   │   ├── phase3.py                 # /phase3/* endpoints
│   │   ├── phase4.py                 # /phase4/* endpoints
│   │   ├── ingestion.py              # /ingestion/* endpoints
│   │   └── shared.py                 # Shared utilities (serialization, input conversion)
│   │
│   └── ingestion/                     # DATA INGESTION PIPELINE
│       ├── memory_embedder.py        # Process pending memories → vectors
│       ├── policy_embedder.py        # Process pending policies → embeddings
│       └── resource_embedder.py      # Chunk resources → embeddings
│
├── *.sql                              # Database migration files
├── requirements.txt                   # Python dependencies
├── Mr_Elite_Postman_Collection.json  # Postman API collection
├── prd.txt                           # Product Requirements Document
├── phase1.txt - phase4.txt           # Phase-specific design docs
└── .gitignore
```

---

## Core Modules

### `app/core/gemini_client.py`, LLM Client

All LLM calls go through this module. Uses **Google Gemini 2.5 Flash**.

| Function | Temperature | Output | Use Case |
|----------|-------------|--------|----------|
| `call_gemini(prompt)` | 0.2 | JSON | Structured classification, planning |
| `call_gemini_text(prompt)` | 0.7 | Natural text | Chat responses, content generation |
| `call_gemini_analysis(prompt)` |, | Structured JSON | Analysis tasks |
| `call_gemini_embedding(text)` |, | 768-dim vector | Semantic search (Gemini Embedding 001) |

### `app/core/supabase_client.py`, Database Client

PostgreSQL + pgvector via Supabase.

- `query_policies_vector()`, Calls `match_policies` RPC for vector similarity search
- `query_resources_vector()`, Calls `match_resources` RPC for resource retrieval
- Service role client (bypasses RLS) for server-side operations
- UUID validation helpers

### `app/core/tool_factory.py`, Tool Instantiation

Creates fresh tool instances with injected OAuth tokens.

- `GmailTool`, Google Gmail API v1
- `CalendarTool`, Google Calendar API v3
- Tokens retrieved from MongoDB per user

### `app/core/tool_tokens.py`, OAuth Token Store

Retrieves OAuth tokens from MongoDB.

- Token lifecycle: access_token, refresh_token, client_id, client_secret
- Injected into Google Credentials for API calls

### `app/core/session/session_service.py`, Session Management

Manages workflow session lifecycle with Supabase persistence.

- **Resolution priority:** clarification_id → session_id → create new
- **Session TTL:** 7 days (604,800 seconds)
- **Clarification TTL:** 1 hour
- State stored in `workflow_states.state_data` (JSONB)

### `app/core/clarification_manager.py`, Multi-Turn Clarification

Handles missing information flows during execution.

- **Domain-specific required fields:**
  - Email: `to`, `subject`, `body`
  - Calendar: `title`, `start_time`
  - Finance: `amount`, `recipient`, `justification`
- LLM-based parsing of user responses to extract field values
- Statuses: `PENDING`, `COMPLETE`, `EXPIRED`, `CANCELLED`

---

## Agents

All agents extend `BaseAgent` (`app/agents/base.py`) and implement:

```python
async def handle_skill(
    capability_id: str,
    args: Dict[str, Any],
    user_id: str,
    tool_tokens: Dict,
    metadata: Optional[Dict] = None
) -> AgentSkillOutput
```

### Email Agent

**Location:** `app/agents/email_agent.py` + `app/agents/email/`

| Skill | Risk | Description |
|-------|------|-------------|
| `send_email` | HIGH | Send message via Gmail |
| `create_draft` | MEDIUM | Create draft |
| `list_threads` | LOW | List conversation threads |
| `get_thread` | LOW | Fetch thread details |

**LangGraph workflow nodes:** `context_fetcher` → `content_generator` → `action_executor`

### Calendar Agent

**Location:** `app/agents/calendar_agent.py` + `app/agents/calendar/`

| Skill | Risk | Description |
|-------|------|-------------|
| `create_event` | MEDIUM | Schedule meeting |
| `list_events` | LOW | List calendar events |
| `update_event` | MEDIUM | Modify event |
| `delete_event` | HIGH | Remove event |

**LangGraph workflow nodes:** `context_fetcher` → `content_generator` → `action_executor`

### Summarizer Microagent

**Location:** `app/microagents/summarizer/`

| Skill | Risk | Description |
|-------|------|-------------|
| `summarize_text` | LOW | Summarize arbitrary text |
| `summarize_emails` | LOW | Summarize email threads |

Can be chained after other agents, receives previous atom output via `metadata["previous_output"]` or `global_context`.

---

## Tools

### GmailTool (`app/tools/gmail.py`)

Direct Google Gmail API v1 wrapper.

```python
class GmailTool:
    def __init__(self, tokens: dict)  # Injects OAuth credentials
    async def send_email(to, subject, body, cc=None, bcc=None)
    async def create_draft(to, subject, body)
    async def list_threads(query=None, max_results=10)
    async def get_thread(thread_id)
```

### CalendarTool (`app/tools/calendar.py`)

Direct Google Calendar API v3 wrapper.

```python
class CalendarTool:
    def __init__(self, tokens: dict)
    async def create_event(title, start_time, end_time, attendees=None, description=None)
    async def list_events(max_results=10, time_min=None, time_max=None, calendar_id="primary")
    async def update_event(event_id, updated_data)
    async def delete_event(event_id)
```

Both tools return structured error responses with `type`, `message`, `operation`, `details` fields.

---

## API Endpoints

### Main Orchestrator

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/orchestrate` | Full 5-phase pipeline execution |

**Request body (`InputPayload`):**
```json
{
  "thread_id": "string",
  "session_id": "string",
  "org_id": "string",
  "workspace_id": "string",
  "user_id": "string",
  "message": "Schedule a meeting with John tomorrow at 2pm",
  "context": [
    {"role": "user", "content": "...", "content_type": "text"}
  ],
  "metadata": {}
}
```

### Phase 1, Planning

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/phase1/test` | Full Phase 1 pipeline with debug output |
| `POST` | `/phase1/classify` | Classification only (no planning/atomization) |
| `POST` | `/phase1/plan` | Planning only (skip classification) |

### Phase 2, Reasoning

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/phase2/test` | Full Phase 2 with debug |
| `POST` | `/phase2/enrich` | Single atom enrichment |
| `POST` | `/phase2/policy` | Policy check only |

### Phase 3, Control

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/phase3/test` | Full Phase 3 with debug |
| `POST` | `/phase3/validate` | Single atom validation |
| `POST` | `/phase3/privacy` | Privacy scan only |
| `POST` | `/phase3/security` | Security scan only |
| `GET`  | `/phase3/pending` | List pending approvals |

### Phase 4, Execution

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/phase4/test` | Full Phase 4 with debug |
| `POST` | `/phase4/dispatch` | Single atom dispatch |
| `GET`  | `/phase4/agents` | List available agents |
| `GET`  | `/phase4/status` | Health check |

### Ingestion

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/ingestion/memory/embed` | Process pending memories into vectors |
| `POST` | `/ingestion/policy/embed` | Process pending policies into embeddings |
| `POST` | `/ingestion/resource/embed` | Process pending resources into embeddings |

---

## Database Schema

### Infrastructure

| Database | Purpose |
|----------|---------|
| **Supabase (PostgreSQL + pgvector)** | Primary data store, vector search, RLS |
| **MongoDB** | OAuth token storage |
| **Redis** | Rate limiting counters, distributed locks |

### Tables by Phase

#### Workspace Definition (Phase 1 reads)

| Table | Purpose |
|-------|---------|
| `organizations` | Organization records |
| `workspaces` | Workspace records |
| `agents` | Available agents per workspace |
| `tools` | Available tools per agent |

#### Phase 2: Memory & Policies

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `policy_rules` | `type`, `rule_text`, `embedding vector(768)`, `similarity_threshold` | Policy storage with vector search. Types: `blocking`, `guiding`, `approval`. RPC: `match_policies()` |
| `memory_items` | `org_id`, `memory_type`, `content`, `importance`, `status`, `expires_at` | User/workflow memories. Types: `state`, `episode`, `semantic`, `preference`, `entity_note` |
| `memory_vectors` | `memory_id FK`, `embedding vector(1536)`, `embedding_model` | Vector embeddings for memories (text-embedding-3-small) |
| `workflow_states` | `session_id`, `state_data JSONB`, `status` | Session state persistence for resumption |
| `resources` | `workspace_id`, `resource_kind`, `status`, `file_url` | Document management (knowledge, templates, configs) |
| `resource_chunks` | `resource_id FK`, `chunk_index`, `content`, `embedding vector(1536)` | Chunked resource vectors |

#### Phase 3: Control & Audit

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `approval_queue` | `atom_payload JSONB`, `risk_level`, `status`, `approval_token`, `expires_at` | Human circuit breaker. Statuses: `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `CANCELLED` |
| `security_audit_logs` | `event_type`, `severity`, `atom_id`, `decision`, `reason` | Immutable event log. Types: `PRIVACY_GUARD`, `SECURITY_GUARD`, `RELIABILITY_GUARD`, `APPROVAL_GATE`, `POLICY_VIOLATION` |

#### Phase 4: Execution

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `execution_results` | `atom_id`, `tool_name`, `runway_type`, `status`, `output JSONB`, `duration_ms`, `trace_id` | All execution outputs. Statuses: `SUCCESS`, `FAILED`, `TIMEOUT`, `RETRYING` |
| `retry_queue` | `atom_id`, `retry_count`, `next_retry_at`, `last_error`, `status` | Failed atoms waiting for retry. Statuses: `PENDING`, `RETRYING`, `EXHAUSTED`, `SUCCESS` |

#### Phase 5: Evaluation & Analytics

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `task_effort_benchmarks` | `task_type UNIQUE`, `category`, `manual_minutes_est`, `ai_minutes_est`, `complexity_weight` | Reference library for MDS calculation |
| `user_analytics_ledger` | `user_id`, `session_id`, `minutes_saved`, `mds_points`, `atoms_executed`, `cost_usd` | Append-only MDS tracking per user |
| `execution_sessions` | `session_id`, `request_summary`, `status`, `total_duration_ms`, `total_cost_usd` | High-level session summaries |

### Migration Files

| File | Content |
|------|---------|
| `migration.sql` | Core tables: `policy_rules`, `match_policies()` RPC |
| `PHASE2_MIGRATION.sql` | Phase 2 tables |
| `PHASE2_MEMORY_MIGRATION.sql` | `memory_items`, `memory_vectors` |
| `PHASE2_STATES_MIGRATION.sql` | State management tables |
| `PHASE3_CONTROL_MIGRATION.sql` | `approval_queue`, `security_audit_logs` |
| `PHASE4_EXECUTION_MIGRATION.sql` | `execution_results`, `retry_queue` |
| `PHASE5_EVALUATION_MIGRATION.sql` | `task_effort_benchmarks`, `user_analytics_ledger`, `execution_sessions` |
| `memory_embedding_migration.sql` | Memory vector indices |

---

## Environment Variables

Create a `.env` file in the project root:

```env
# ─── Required ───
GEMINI_API_KEY=your_gemini_api_key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key
GOOGLE_CLIENT_ID=your_google_oauth_client_id
GOOGLE_CLIENT_SECRET=your_google_oauth_client_secret
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/dbname

# ─── Optional ───
REDIS_URL=redis://localhost:6379
LOG_LEVEL=INFO
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Framework** | FastAPI + Uvicorn |
| **LLM** | Google Gemini 2.5 Flash |
| **Embeddings** | Gemini Embedding 001 (768-dim for policies) |
| **Vector DB** | Supabase PostgreSQL + pgvector (HNSW/IVFFlat indices) |
| **Primary DB** | Supabase PostgreSQL |
| **Token Store** | MongoDB |
| **Rate Limiting** | Redis |
| **Agent Graphs** | LangGraph |
| **Validation** | Pydantic v2 |
| **Google APIs** | google-auth + google-api-python-client (Gmail v1, Calendar v3) |
| **DAG Operations** | NetworkX |
| **Document Processing** | PyPDF, python-docx |
| **Token Counting** | tiktoken |

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- Supabase project with pgvector enabled
- MongoDB instance (for OAuth token storage)
- Redis instance (for rate limiting)
- Google Cloud project with Gmail and Calendar APIs enabled
- Google Gemini API key

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/therohitji/mr-elite-orchestrator.git
cd mr-elite-orchestrator

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate          # Linux/Mac
# venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env              # Then fill in your values

# 5. Run database migrations
# Execute each SQL file in your Supabase SQL editor in order:
#   migration.sql
#   PHASE2_MIGRATION.sql
#   PHASE2_MEMORY_MIGRATION.sql
#   PHASE2_STATES_MIGRATION.sql
#   memory_embedding_migration.sql
#   PHASE3_CONTROL_MIGRATION.sql
#   PHASE4_EXECUTION_MIGRATION.sql
#   PHASE5_EVALUATION_MIGRATION.sql

# 6. Start the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

---

## End-to-End Data Flow Example

**User prompt:** _"Fetch resumes from hr@team.com, shortlist by our hiring policy, schedule interviews with top candidates, and send rejection emails to the rest."_

### Phase 1: Planning
1. **Pre-flight**, Not globally blocked → pass
2. **Heuristic**, No regex match → escalate to LLM
3. **Semantic classify**, `Intent=GOAL`, `complexity=8`, `domain=hr`, `risk=HIGH`
4. **CoT plan**, Produces 5 steps:
   - A: Fetch emails (Email Agent, `list_threads`)
   - B: Extract candidate data (Data Extraction Agent)
   - C: Apply hiring policy (Policy evaluation)
   - D: Schedule interviews (Calendar Agent, `create_event`)
   - E: Send rejections (Email Agent, `send_email`)
   - Dependencies: `A → B → C → (D, E)`
5. **Atomize**, 5 `ExecutionAtom` objects with UUIDs + dependency graph

### Phase 2: Reasoning (parallel)
- **Thread A:** Policy engine finds hiring policies via vector search; resource engine fetches rejection email template
- **Thread B:** Memory engine recalls past hiring preferences; state check confirms budget is open
- **Result:** Atoms A/B/C enriched and APPROVED. Atom E marked PAUSED (send_email is HIGH risk + external recipients)

### Phase 3: Control
1. **Privacy guard**, Scans for PII in candidate data → redacts phone numbers
2. **Security guard**, Checks email content for prompt injection → pass
3. **Reliability guard**, Estimates cost within budget → pass
4. **Approval gate**, Atom E requires human approval → held in `approval_queue`

### Phase 4: Execution
1. Execute A: Fetch 50 resume threads (Gmail, ~2s)
2. Execute B: Extract candidate data (LLM, ~5s)
3. Execute C: Apply policy filter (Logic, ~1s)
4. Execute D: Schedule 10 interviews (Calendar, ~10s)
5. **Await approval** → User approves Atom E
6. Execute E: Send 40 rejection emails (Gmail, ~30s)

### Phase 5: Evaluation (background)
1. **Validate**, 5/5 atoms succeeded
2. **MDS**, ~99 minutes saved, MDS ≈ 250 points
3. **Analytics**, Logged to `user_analytics_ledger`
4. **Learning**, User edits rejection text → preference rule extracted and stored

---

## Key Design Patterns

### Idempotency
Every atom carries `idempotency_key = atom.id`. Tools support safe retry. Deduplication prevents double execution.

### Dependency Graph (DAG)
`ExecutionAtom.dependencies: List[UUID]` defines the execution graph. Topological sort determines order. Independent atoms run in parallel. Failed atoms block their dependents.

### Risk Classification

| Level | Description | Examples |
|-------|-------------|---------|
| `LOW` | Read-only, no side effects | `list_events`, `get_thread` |
| `MEDIUM` | Modifies data, reversible | `create_draft`, `update_event` |
| `HIGH` | Irreversible, external, financial | `send_email`, `delete_event` |
| `CRITICAL` | Requires explicit approval | Org-level actions |

### Multi-Tenancy
All tables include `org_id` and `workspace_id`. Row-Level Security (RLS) enforces isolation. Service role client used for server-side operations.

### Re-Planning Triggers
- Missing resource → FAIL atom + replanning loop
- Unclear policy violation → Escalate for review
- Atom execution failure with `replan_required=True`
- Missing information → Create `human_input` atom + wait for clarification

### Error Recovery
- **Phase 1:** Invalid intent → early exit with chat response
- **Phase 2:** Missing resource → FAIL atom + trigger replanning
- **Phase 3:** PII/injection detected → REDACT or BLOCK
- **Phase 4:** Tool timeout → retryable error + exponential backoff (max 3 retries)
- **Phase 5:** Execution failed → log to analytics + offer retry
- **Dead letter queue:** Atoms exhausting retries go to `retry_queue` with `status=EXHAUSTED`

---

## Ingestion Pipeline

Separate from the 5-phase pipeline. Processes uploaded data into vector-searchable form.

```
User uploads data → marked PENDING/UPLOADED
         │
         ▼
POST /ingestion/{type}/embed
         │
         ▼
┌─────────────────────────────────────┐
│  1. Fetch all PENDING items          │
│  2. Download/extract content         │
│  3. Generate embeddings (Gemini)     │
│  4. Store in vector table            │
│  5. Mark as READY or FAILED          │
└─────────────────────────────────────┘
         │
         ▼
Return result summary
```

| Embedder | Input Table | Output Table | Embedding Dim |
|----------|------------|--------------|---------------|
| `memory_embedder.py` | `memory_items` | `memory_vectors` | 1536 |
| `policy_embedder.py` | `policy_rules` | inline `embedding` column | 768 |
| `resource_embedder.py` | `resources` | `resource_chunks` | 1536 |

---

## License

Private repository. All rights reserved.

</details>

<!-- presentation:end -->
