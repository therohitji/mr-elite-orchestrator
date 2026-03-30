# app/phase4/dispatcher.py
"""
PHASE 4: CENTRAL DISPATCHER (SIMPLIFIED)

Routes atoms to agent handlers based on:
1. atom.target_agent → email_agent, calendar_agent, etc.
2. atom.tool_arguments.operation → skill_id for the agent

No more TOOL_REGISTRY dependency - everything comes from Supabase via atoms.
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional
from uuid import UUID, uuid4
from enum import Enum

from app.phase1.atoms import ExecutionAtom
from app.core.execution.context import global_context
from app.phase4.result_store import ResultStore
from app.phase4.retry_engine import RetryEngine
from app.core.gemini_client import call_gemini
from app.core.tool_tokens import get_tool_token

logger = logging.getLogger(__name__)

EXECUTION_TIMEOUT_SECONDS = 30


class ExecutionStatus(str, Enum):
    """Phase 4 execution outcomes"""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    BLOCKED = "BLOCKED"
    RETRYABLE = "RETRYABLE"


class DispatcherResult:
    """Structured execution result"""

    def __init__(
        self,
        atom_id: UUID,
        status: ExecutionStatus,
        output: Any = None,
        error: Optional[str] = None,
        trace_id: Optional[str] = None,
        duration_ms: float = 0.0,
        retryable: bool = False,
    ):
        self.atom_id = atom_id
        self.status = status
        self.output = output
        self.error = error
        self.trace_id = trace_id or str(uuid4())
        self.duration_ms = duration_ms
        self.retryable = retryable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atom_id": str(self.atom_id),
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "trace_id": self.trace_id,
            "duration_ms": self.duration_ms,
            "retryable": self.retryable,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT NAME → HANDLER MAPPING
# This replaces TOOL_REGISTRY - just map agent names to their handlers
# ═══════════════════════════════════════════════════════════════════════════════
AGENT_HANDLERS = {
    # Domain agents (from your Supabase agents table)
    "email agent": "email_agent",
    "calendar agent": "calendar_agent",
    "documentation agent": "documentation_agent",
    
    # Micro agents
    "data extraction agent": "data_extractor_agent",
    "writing agent": "writing_agent",
    "summariser agent": "summarizer_agent",
    "summarizer agent": "summarizer_agent",  # Both spellings
    
    # System/fallback
    "system": None,  # No external handler needed
}

# ═══════════════════════════════════════════════════════════════════════════════
# OPERATION → SKILL_ID MAPPING  
# Maps Supabase tool operations to agent skill IDs
# ═══════════════════════════════════════════════════════════════════════════════
OPERATION_TO_SKILL = {
    # Gmail operations
    "send": "send_email",
    "create_draft": "create_draft",
    "list_recent_threads": "list_threads",
    "fetch_thread": "get_thread",
    "search": "search_emails",
    "read": "get_thread",
    
    # Calendar operations
    "list_events": "list_events",
    "create_event": "create_event",
    "update_event": "update_event",
    "delete_event": "delete_event",
    "get_free_busy": "get_free_busy",
    "search_events": "search_events",
    
    # Summarizer operations
    "summarize": "summarize_text",
    "summarize_emails": "summarize_emails",
}


class CentralDispatcher:
    """
    Simplified routing engine for ExecutionAtoms.
    
    Uses atom.target_agent to determine which agent to call.
    Uses atom.tool_arguments.operation to determine which skill.
    
    No more TOOL_REGISTRY dependency!
    """

    def __init__(self, org_id: str, workspace_id: str, request_id: str):
        self.org_id = org_id
        self.workspace_id = workspace_id
        self.request_id = request_id
        self.timeout_seconds = EXECUTION_TIMEOUT_SECONDS
        self.execution_cache: Dict[str, DispatcherResult] = {}
        
        # Initialize database-backed stores
        self.result_store = ResultStore(org_id, workspace_id)
        self.retry_engine = RetryEngine(org_id, workspace_id)

    async def dispatch(self, atom: ExecutionAtom) -> DispatcherResult:
        """
        Main entry point: Route atom to correct agent handler.
        """
        start_time = time.time()
        trace_id = str(uuid4())

        # ─────────────────── DEBUG CONTEXT ──────────────────
        print(f"\n[DEBUG DISPATCHER] Received Context:")
        print(f"  Atom ID: {atom.id}")
        print(f"  Tool Name: {atom.tool_name}")
        print(f"  Target Agent: {atom.target_agent}")
        print(f"  Tool Arguments: {atom.tool_arguments}")
        print(f"  Trace ID: {trace_id}")

        try:
            # ─────────────────── IDEMPOTENCY CHECK ──────────────────
            if atom.idempotency_key and atom.idempotency_key in self.execution_cache:
                return self.execution_cache[atom.idempotency_key]

            # ─────────────────── SKIP UI ATOMS ──────────────────
            if atom.target_agent == "UI_DISPLAY":
                return DispatcherResult(
                    atom_id=atom.id,
                    status=ExecutionStatus.SUCCESS,
                    output={
                        "status": "ui_message",
                        "content": atom.tool_arguments.get("content", ""),
                    },
                    trace_id=trace_id,
                )

            # ─────────────────── ROUTE BY AGENT NAME ──────────────────
            agent_name = (atom.target_agent or "").lower().strip()
            agent_handler_name = AGENT_HANDLERS.get(agent_name)
            
            if agent_handler_name:
                # Route to specialized agent handler
                result = await self._execute_with_agent(
                    agent_handler_name, atom, trace_id
                )
            elif agent_name == "system" or not agent_name:
                # System/fallback - use LLM directly
                result = await self._execute_with_llm(atom, trace_id)
            else:
                # Unknown agent - try LLM fallback
                logger.warning(f"[{trace_id}] Unknown agent '{agent_name}', using LLM fallback")
                result = await self._execute_with_llm(atom, trace_id)

            # ─────────────────── IDEMPOTENCY CACHE ──────────────────
            if atom.idempotency_key:
                self.execution_cache[atom.idempotency_key] = result

            # ─────────────────── DURATION ──────────────────
            duration_ms = (time.time() - start_time) * 1000
            result.duration_ms = duration_ms

            # ─────────────────── STORE RESULT ──────────────────
            await self.result_store.store_result(
                atom_id=atom.id,
                request_id=self.request_id,
                tool_name=atom.tool_name,
                runway_type="AI_AGENT",
                status=result.status.value,
                output=result.output,
                error=result.error,
                duration_ms=duration_ms,
                retries=0,
                trace_id=trace_id,
            )

            # ─────────────────── LOG EVENT ──────────────────
            await self.result_store.log_execution_event(
                atom_id=atom.id,
                request_id=self.request_id,
                event_type="COMPLETE" if result.status == ExecutionStatus.SUCCESS else "ERROR",
                message=f"Atom execution completed: {result.status.value}",
                metadata={
                    "tool_name": atom.tool_name,
                    "target_agent": atom.target_agent,
                    "duration_ms": duration_ms,
                },
            )

            # ─────────────────── WRITE TO GLOBAL CONTEXT ──────────────────
            output_var = getattr(atom, "output_variable", None)
            if result.status == ExecutionStatus.SUCCESS and output_var:
                global_context.set(output_var, result.output)

            return result

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"[{trace_id}] Atom {atom.id} TIMEOUT after {self.timeout_seconds}s")

            result = DispatcherResult(
                atom_id=atom.id,
                status=ExecutionStatus.TIMEOUT,
                error=f"Execution exceeded {self.timeout_seconds}s timeout",
                trace_id=trace_id,
                duration_ms=duration_ms,
                retryable=True,
            )

            await self._store_failure(atom, result, trace_id)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"[{trace_id}] Atom {atom.id} FAILED: {str(e)}", exc_info=True)

            is_retryable = self.retry_engine.is_retryable_error(str(e))
            
            result = DispatcherResult(
                atom_id=atom.id,
                status=ExecutionStatus.FAILED,
                error=str(e),
                trace_id=trace_id,
                duration_ms=duration_ms,
                retryable=is_retryable,
            )

            await self._store_failure(atom, result, trace_id)
            return result

    async def _execute_with_agent(
        self, agent_handler_name: str, atom: ExecutionAtom, trace_id: str
    ) -> DispatcherResult:
        """
        Execute with specialized agent handler.
        Uses atom.tool_arguments.operation to determine the skill.
        """
        print(f"\n[DEBUG AGENT EXECUTION] Agent: {agent_handler_name}")
        print(f"  Tool: {atom.tool_name}")
        print(f"  Operation: {atom.tool_arguments.get('operation', 'N/A')}")

        try:
            # Import the appropriate agent
            handler = await self._import_agent_handler(agent_handler_name)
            
            if not handler:
                raise ValueError(f"Could not import agent handler: {agent_handler_name}")

            # Get user_id from atom context
            user_id = atom.context_data.get("user_id", "default_user")

            # Get connector for token lookup (from tool_arguments or tool_name)
            connector = atom.tool_arguments.get("connector", "").lower()
            if not connector:
                # Parse from tool_name (e.g., "calendar.list_events" -> "calendar")
                if "." in atom.tool_name:
                    connector = atom.tool_name.split(".")[0].lower()
                elif "gmail" in atom.tool_name.lower() or "email" in atom.tool_name.lower():
                    connector = "gmail"
                elif "calendar" in atom.tool_name.lower():
                    connector = "calendar"

            # Agents that don't require external API tokens
            NO_TOKEN_AGENTS = {"summarizer_agent", "writing_agent", "data_extractor_agent"}
            
            tool_tokens = None
            if agent_handler_name not in NO_TOKEN_AGENTS:
                logger.info(f"🔐 [PHASE4] Fetching tokens for connector={connector}, user_id={user_id}")
                tool_tokens = get_tool_token(tool_name=connector, user_id=user_id)
                
                if not tool_tokens:
                    raise ValueError(f"No tokens found for connector '{connector}'")
                logger.info(f"✅ [PHASE4] Tokens retrieved successfully")

            # Get skill_id from operation
            operation = atom.tool_arguments.get("operation", "").lower()
            skill_id = OPERATION_TO_SKILL.get(operation, operation)
            
            logger.info(f"🎯 [PHASE4] Mapped operation '{operation}' -> skill_id '{skill_id}'")

            # Execute the agent's handle_skill method
            result = await asyncio.wait_for(
                handler.handle_skill(
                    skill_id=skill_id,
                    args=atom.tool_arguments,
                    user_id=user_id,
                    tool_tokens=tool_tokens,
                    metadata=atom.context_data,
                ),
                timeout=self.timeout_seconds,
            )

            return DispatcherResult(
                atom_id=atom.id,
                status=ExecutionStatus.SUCCESS,
                output=result,
                trace_id=trace_id,
            )

        except asyncio.TimeoutError:
            raise
        except Exception as e:
            logger.error(f"[{trace_id}] Agent handler {agent_handler_name} error: {e}")
            raise

    async def _import_agent_handler(self, agent_handler_name: str):
        """Dynamically import agent handler by name."""
        try:
            if agent_handler_name == "email_agent":
                from app.agents.email_agent import email_agent
                return email_agent
            elif agent_handler_name == "calendar_agent":
                from app.agents.calendar_agent import calendar_agent
                return calendar_agent
            elif agent_handler_name == "summarizer_agent":
                from app.microagents.summarizer.summarizer_agent import summarizer_agent
                return summarizer_agent
            elif agent_handler_name == "documentation_agent":
                # Add when available
                logger.warning(f"Documentation agent not yet implemented")
                return None
            elif agent_handler_name == "writing_agent":
                # Add when available
                logger.warning(f"Writing agent not yet implemented")
                return None
            elif agent_handler_name == "data_extractor_agent":
                # Add when available
                logger.warning(f"Data extractor agent not yet implemented")
                return None
            else:
                return None
        except ImportError as e:
            logger.error(f"Failed to import agent handler {agent_handler_name}: {e}")
            return None

    async def _execute_with_llm(
        self, atom: ExecutionAtom, trace_id: str
    ) -> DispatcherResult:
        """
        Fallback: Execute using LLM directly when no agent handler available.
        """
        prompt = (
            f"You are executing a task.\n\n"
            f"Tool: {atom.tool_name}\n"
            f"Description: {atom.description}\n"
            f"Arguments: {atom.tool_arguments}\n"
            f"Context: {atom.context_data}\n\n"
            f"Please execute this task and provide the result."
        )

        response = await asyncio.wait_for(
            asyncio.to_thread(call_gemini, prompt),
            timeout=self.timeout_seconds
        )

        output = {
            "status": "ok",
            "tool": atom.tool_name,
            "agent_output": response,
            "execution_type": "llm_fallback",
        }

        return DispatcherResult(
            atom_id=atom.id,
            status=ExecutionStatus.SUCCESS,
            output=output,
            trace_id=trace_id,
        )

    async def _store_failure(
        self, atom: ExecutionAtom, result: DispatcherResult, trace_id: str
    ):
        """Store failure result and optionally queue for retry."""
        await self.result_store.store_result(
            atom_id=atom.id,
            request_id=self.request_id,
            tool_name=atom.tool_name,
            runway_type="AI_AGENT",
            status=result.status.value,
            error=result.error,
            duration_ms=result.duration_ms,
            trace_id=trace_id,
        )

        if result.retryable:
            await self.retry_engine.enqueue_for_retry(
                atom_id=atom.id,
                request_id=self.request_id,
                tool_name=atom.tool_name,
                tool_arguments=atom.tool_arguments,
                runway_type="AI_AGENT",
                error=result.error,
                retry_count=0,
            )
