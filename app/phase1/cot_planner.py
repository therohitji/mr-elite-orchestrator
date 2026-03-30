# app/phase1/cot_planner.py

import json
import logging
from typing import Dict, List, Any, Optional

from app.core.gemini_client import call_gemini
from app.phase1.cot_prompt import COT_SYSTEM_PROMPT, REPLAN_PROMPT_TEMPLATE
from app.phase1.plans import ExecutionPlan
from app.phase1.workspace_cache import workspace_cache

logger = logging.getLogger(__name__)


def _format_result_for_prompt(result: dict) -> str:
    """Format execution result for inclusion in re-planning prompt."""
    if not result:
        return "No data returned"
    
    # Convert to readable format, truncate if too long
    result_str = json.dumps(result, indent=2, default=str)
    
    # Truncate to prevent token bloat (keep first 2000 chars)
    if len(result_str) > 2000:
        result_str = result_str[:2000] + "\n... [truncated]"
    
    return result_str


def _build_agent_tool_context(agents_with_tools: List[Dict[str, Any]]) -> str:
    """
    Build the agent/tool context section for the prompt.
    
    Args:
        agents_with_tools: List of agents with their tools
        
    Returns:
        Formatted string for prompt injection
    """
    if not agents_with_tools:
        return ""
    
    lines = [
        "\n" + "=" * 80,
        "AVAILABLE AGENTS AND TOOLS (CRITICAL: Use ONLY these exact names)",
        "=" * 80,
    ]
    
    for agent in agents_with_tools:
        agent_name = agent.get("name", "Unknown")
        agent_desc = agent.get("description", "")
        tools = agent.get("tools", [])
        
        lines.append(f"\n📦 Agent: \"{agent_name}\"")
        if agent_desc:
            lines.append(f"   Description: {agent_desc}")
        
        lines.append("   Tools:")
        for tool in tools:
            tool_code = tool.get("code", "")
            tool_desc = tool.get("description", "")
            lines.append(f"     • \"{tool_code}\": {tool_desc}")
    
    lines.extend([
        "\n" + "=" * 80,
        "INSTRUCTIONS:",
        "  1. Use ONLY the agent names and tool codes listed above (exact match required)",
        "  2. For 'agent' field: use the exact agent name in quotes (e.g., \"Gmail Agent\")",
        "  3. For 'tool' field: use the exact tool code (e.g., \"send_email\")",
        "  4. If no tool matches your need, use agent=\"System\", tool=\"human_input\"",
        "=" * 80,
    ])
    
    return "\n".join(lines)


async def run_cot_planner(
    user_input: str,
    workspace_id: str = "default",
    context: dict | None = None,
    supabase_client=None,
) -> ExecutionPlan:
    """
    Runs Chain-of-Thought planning for GOAL-type requests.
    
    Loads available agents and tools from workspace cache,
    injects them into the prompt, and the LLM outputs exact agent/tool names.

    Args:
        user_input: User's goal/task description
        workspace_id: Workspace to load agents/tools from
        context: Additional context (optional). For re-planning:
            - is_replan: True
            - original_goal: Original user message
            - previous_atom_description: What the previous atom did
            - previous_result: Raw output from previous atom
            - pending_goal: What to decide next
        supabase_client: Supabase client (optional, will auto-import)

    Returns:
        ExecutionPlan with agent/tool-based steps
    """
    # Get supabase client if not provided
    if supabase_client is None:
        try:
            from app.core.supabase_client import supabase_client as sb_client
            supabase_client = sb_client
        except ImportError:
            logger.warning("Supabase client not available")

    # 🔹 Load available agents and tools from workspace CACHE
    logger.info(f"[COT PLANNER] Loading agents/tools for workspace: {workspace_id}")
    cached_data = await workspace_cache.get_agents_and_tools(workspace_id, supabase_client)
    agents_with_tools = cached_data.get("agents", [])
    
    # 🔹 Build agent/tool context for prompt
    agent_tool_context = _build_agent_tool_context(agents_with_tools)

    # 🔹 Retrieve policy context (existing logic)
    policy_context = await _retrieve_policy_context(supabase_client)

    # 🔹 Build chat history context if provided
    chat_history_context = _build_chat_history_context(context)

    # 🔹 Check if this is a re-planning call
    is_replan = context.get("is_replan", False) if context else False

    # 🔹 Assemble final prompt
    prompt_parts = [COT_SYSTEM_PROMPT, agent_tool_context]

    if is_replan:
        # Add re-planning context for decision point continuations
        replan_context = REPLAN_PROMPT_TEMPLATE.format(
            original_goal=context.get("original_goal", user_input),
            previous_description=context.get("previous_atom_description", "Unknown action"),
            previous_result=_format_result_for_prompt(context.get("previous_result", {})),
            pending_goal=context.get("pending_goal", user_input),
        )
        prompt_parts.append(replan_context)
        logger.info("[COT PLANNER] Re-planning mode activated - using decision context")

    if policy_context:
        prompt_parts.append(f"\n\nRelevant Policies:\n{policy_context}")

    if chat_history_context:
        prompt_parts.append(chat_history_context)

    prompt_parts.append(f"\n\nUser Goal:\n{user_input}")

    prompt = "\n".join(prompt_parts)

    logger.info(
        f"[COT PLANNER] Calling Gemini with {len(agents_with_tools)} agents..."
    )

    # 🔹 Call LLM
    raw_output = call_gemini(prompt)

    # Log the raw output for debugging
    logger.info(
        f"[COT PLANNER] Raw Gemini output: {raw_output[:500]}"
    )  # First 500 chars

    # 🔹 Parse and validate
    try:
        # Clean JSON if wrapped in markdown code blocks
        cleaned = raw_output.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        
        parsed = json.loads(cleaned.strip())
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON from CoT planner: {raw_output}")

    logger.info(f"[COT PLANNER] Plan created with {len(parsed.get('plan', []))} steps")

    return ExecutionPlan(**parsed)


async def _retrieve_policy_context(supabase_client=None) -> str:
    """
    Query Supabase policies table to retrieve relevant context for CoT reasoning.
    Returns formatted policy string for prompt injection.
    """
    if supabase_client is None:
        return ""

    try:
        # Query guiding policies for context
        results = (
            supabase_client.table("policy_rules")
            .select("*")
            .eq("policy_type", "guiding")
            .limit(5)
            .execute()
        )

        if results.data:
            policies_text = "\n".join([p["rule_text"] for p in results.data])
            return policies_text

        return ""
    except Exception as e:
        logger.warning(f"Policy context retrieval error: {e}")
        return ""


def _build_chat_history_context(context: dict | None) -> str:
    """
    Build chat history context for prompt injection.
    Includes thread_history (rolling summary + recent exchanges) and any chat messages.
    """
    if not context:
        return ""

    thread_history = context.get("thread_history", "")
    chat_history = context.get("chat_history", [])
    previous_atoms = context.get("previous_atoms", [])

    if not thread_history and not chat_history and not previous_atoms:
        return ""

    parts = ["\n" + "=" * 80, "CONVERSATION CONTEXT (USE THIS TO EXTRACT PARAMETERS)", "=" * 80]

    if thread_history:
        parts.append("\n" + thread_history)
        parts.append(
            "\n⚠️ IMPORTANT: If the user references previous actions (e.g., 'send the email you drafted', "
            "'use the same details'), you MUST extract the actual parameter values (to, subject, body, "
            "event title, attendees, etc.) from the conversation context above and include them in your plan. "
            "NEVER create a plan with missing parameters when they are available in the conversation history."
        )

    if chat_history:
        parts.append("\nAdditional Messages:")
        for msg in chat_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if len(content) > 500:
                content = content[-500:] + " [truncated]"
            parts.append(f"  {role}: {content}")

    if previous_atoms:
        parts.append("\nPrevious Actions (for reference):")
        for atom in previous_atoms[-5:]:
            desc = atom.get("description", "")
            status = atom.get("status", "")
            parts.append(f"  - {desc} [{status}]")

    parts.append("=" * 80)
    return "\n".join(parts)
