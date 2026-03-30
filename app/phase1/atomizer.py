# app/phase1/atomizer.py
"""
Simplified Atomizer: Creates ExecutionAtoms from LLM-generated plans.

The new flow:
1. LLM outputs exact agent names and tool codes
2. Atomizer validates they exist in the workspace
3. Creates atoms directly without mapping/resolution logic
"""

import uuid
import re
import logging
import networkx as nx
from typing import Optional, Dict, List, Any

from app.phase1.atoms import ExecutionAtom
from app.core.supabase_client import supabase_client
from app.phase1.workspace_cache import workspace_cache

logger = logging.getLogger(__name__)


class AtomizerEngine:
    """
    Simplified atomizer that converts LLM plans to ExecutionAtoms.
    
    Since LLM now outputs exact agent/tool names from the injected workspace context,
    we only need to validate and create atoms - no mapping required.
    """
    
    async def atomize(
        self,
        execution_plan,
        workspace_id: str,
        user_id: str = None,
        user_input: str = None,
    ) -> Dict[str, Any]:
        """
        Convert an execution plan to a list of ExecutionAtoms.
        
        Args:
            execution_plan: ExecutionPlan with agent/tool-based steps
            workspace_id: Workspace ID for validation
            user_id: User ID for context
            user_input: Original user input for context
            
        Returns:
            Dict with:
                - atoms: List of ExecutionAtom objects
                - matching_summary: Debug info about each step
                - total_steps: Number of input steps
                - atoms_created: Number of atoms successfully created
        """
        atoms = []
        step_id_to_uuid = {}
        matching_summary = []
        
        # Load valid agents and tools for validation
        valid_agents = await self._load_agents(workspace_id)
        valid_tools = await self._load_tools(workspace_id)
        
        # Create lookup dictionaries for fast validation
        agent_lookup = {a["name"].lower(): a for a in valid_agents}
        tool_lookup = {t["code"].lower(): t for t in valid_tools}
        
        logger.info(f"[ATOMIZER] Processing {len(execution_plan.plan)} steps...")
        logger.info(f"[ATOMIZER] Valid agents: {list(agent_lookup.keys())}")
        logger.info(f"[ATOMIZER] Valid tools: {list(tool_lookup.keys())}")
        
        for step in execution_plan.plan:
            atom_id = uuid.uuid4()
            step_id_to_uuid[step.step_id] = atom_id
            
            step_agent = step.agent.strip() if step.agent else ""
            step_tool = step.tool.strip() if step.tool else ""
            
            logger.info(f"[ATOM {step.step_id}] Agent: '{step_agent}', Tool: '{step_tool}'")
            logger.info(f"[ATOM {step.step_id}] Description: {step.description}")
            
            # Handle human_input (clarification) special case
            if step_tool.lower() == "human_input" or step_agent.lower() == "system":
                logger.info(f"[ATOM {step.step_id}] 🙋 HUMAN_INPUT: Creating clarification atom")
                atom = ExecutionAtom(
                    id=atom_id,
                    description=step.description,
                    target_agent="HUMAN_USER",
                    tool_name="human_input",
                    tool_arguments={
                        "input_type": "clarification",
                        "prompt": step.description,
                    },
                    dependencies=[],
                    risk_level="low",
                    status="PAUSED",
                    tags=["human_input", "clarification"],
                    context_data={"user_id": user_id},
                    requires_replan=getattr(step, 'requires_replan', False),
                    pending_goal=getattr(step, 'pending_goal', None),
                    original_user_goal=user_input,
                )
                atoms.append(atom)
                matching_summary.append({
                    "step_id": step.step_id,
                    "agent": step_agent,
                    "tool": step_tool,
                    "status": "CREATED",
                    "type": "HUMAN_INPUT",
                })
                continue
            
            # Validate agent exists
            agent_key = step_agent.lower()
            agent = agent_lookup.get(agent_key)
            
            if not agent:
                logger.warning(f"[ATOM {step.step_id}] ❌ Invalid agent: '{step_agent}' not found")
                matching_summary.append({
                    "step_id": step.step_id,
                    "agent": step_agent,
                    "tool": step_tool,
                    "status": "REJECTED",
                    "reason": f"Agent '{step_agent}' not found in workspace",
                })
                continue
            
            # Validate tool exists
            tool_key = step_tool.lower()
            tool = tool_lookup.get(tool_key)
            
            if not tool:
                logger.warning(f"[ATOM {step.step_id}] ❌ Invalid tool: '{step_tool}' not found")
                matching_summary.append({
                    "step_id": step.step_id,
                    "agent": step_agent,
                    "tool": step_tool,
                    "status": "REJECTED",
                    "reason": f"Tool '{step_tool}' not found in workspace",
                })
                continue
            
            # Both agent and tool are valid - create atom
            logger.info(f"[ATOM {step.step_id}] ✅ Valid: {step_agent} / {step_tool}")
            
            # Use parameters from step if provided by LLM, otherwise extract from description
            if hasattr(step, 'parameters') and step.parameters:
                tool_args = dict(step.parameters)  # Use LLM-provided parameters
                logger.info(f"[ATOM {step.step_id}] Using LLM-provided parameters: {list(tool_args.keys())}")
            else:
                tool_args = self._extract_tool_arguments(step_tool, step.description, user_input)
            
            # Add tool metadata
            tool_args["tool_id"] = tool.get("id", "")
            tool_args["connector"] = tool.get("connector", "")
            tool_args["operation"] = tool.get("operation", "")
            
            # Sanitize description (remove PII)
            sanitized_description = self._sanitize_description(step.description)
            
            atom = ExecutionAtom(
                id=atom_id,
                description=sanitized_description,
                target_agent=agent["name"],  # Use canonical name from DB
                tool_name=tool["code"],       # Use canonical code from DB
                tool_arguments=tool_args,
                dependencies=[],  # Will be resolved below
                risk_level=tool.get("risk_level", "medium").lower(),
                status="PENDING",
                tags=[],
                context_data={
                    "user_id": user_id,
                    "agent_id": agent.get("id", ""),
                    "tool_id": tool.get("id", ""),
                },
                requires_replan=getattr(step, 'requires_replan', False),
                pending_goal=getattr(step, 'pending_goal', None),
                original_user_goal=user_input,
            )
            
            atoms.append(atom)
            matching_summary.append({
                "step_id": step.step_id,
                "agent": step_agent,
                "tool": step_tool,
                "status": "CREATED",
                "type": "AGENT_EXECUTABLE",
            })
        
        # Resolve dependencies (step_id -> atom UUID)
        for step in execution_plan.plan:
            if step.step_id in step_id_to_uuid:
                atom_uuid = step_id_to_uuid[step.step_id]
                atom = next((a for a in atoms if a.id == atom_uuid), None)
                if atom and step.dependencies:
                    atom.dependencies = [
                        step_id_to_uuid[dep_id]
                        for dep_id in step.dependencies
                        if dep_id in step_id_to_uuid
                    ]
        
        # Validate DAG (no cycles)
        self._validate_dag(atoms)
        
        logger.info(f"[ATOMIZER] Created {len(atoms)} atoms from {len(execution_plan.plan)} steps")
        
        return {
            "atoms": atoms,
            "matching_summary": matching_summary,
            "total_steps": len(execution_plan.plan),
            "atoms_created": len(atoms),
            "atoms_rejected": len(execution_plan.plan) - len(atoms),
        }
    
    async def _load_agents(self, workspace_id: str) -> List[Dict]:
        """Load active agents for the workspace from cache."""
        cached_data = await workspace_cache.get_agents_and_tools(workspace_id, supabase_client)
        return cached_data.get("agents", [])
    
    async def _load_tools(self, workspace_id: str) -> List[Dict]:
        """Load active tools for the workspace from cache."""
        cached_data = await workspace_cache.get_agents_and_tools(workspace_id, supabase_client)
        # Flatten tools from all agents
        all_tools = []
        for agent in cached_data.get("agents", []):
            all_tools.extend(agent.get("tools", []))
        return all_tools
    
    def _extract_tool_arguments(self, tool_code: str, description: str, user_input: str) -> Dict[str, Any]:
        """
        Extract tool-specific arguments using the param_extractor module.
        
        Uses LLM-based extraction for accurate parameter parsing from natural language.
        """
        args = {}
        
        # Import param_extractor functions
        from app.phase1.param_extractor import (
            extract_event_parameters,
            extract_email_parameters,
            extract_list_events_parameters,
            extract_update_event_parameters,
        )
        
        # Normalize tool code for matching
        tool_lower = tool_code.lower()
        
        # Calendar event creation
        if tool_lower in ["event_create", "calendar.create_event", "create_event"]:
            logger.info(f"[PARAM_EXTRACTOR] Calling extract_event_parameters for: {description[:50]}...")
            extracted = extract_event_parameters(description)
            args.update({
                "title": extracted.get("title"),
                "start_time": extracted.get("start_time"),
                "end_time": extracted.get("end_time"),
                "attendees": extracted.get("attendees", []),
            })
            logger.info(f"[PARAM_EXTRACTOR] Extracted calendar params: {args}")
        
        # Calendar event update
        elif tool_lower in ["event_update", "calendar.update_event", "update_event"]:
            logger.info(f"[PARAM_EXTRACTOR] Calling extract_update_event_parameters for: {description[:50]}...")
            extracted = extract_update_event_parameters(description)
            args.update({
                "event_id": extracted.get("event_id"),
                "new_start": extracted.get("new_start"),
                "new_end": extracted.get("new_end"),
                "new_title": extracted.get("new_title"),
            })
            logger.info(f"[PARAM_EXTRACTOR] Extracted update params: {args}")
        
        # Calendar event list
        elif tool_lower in ["event_list", "calendar.list_events", "list_events"]:
            logger.info(f"[PARAM_EXTRACTOR] Calling extract_list_events_parameters for: {description[:50]}...")
            extracted = extract_list_events_parameters(description)
            args.update({
                "date_range": extracted.get("date_range", "today"),
            })
            logger.info(f"[PARAM_EXTRACTOR] Extracted list params: {args}")
        
        # Email send
        elif tool_lower in ["send_email", "gmail.send", "email_send"]:
            logger.info(f"[PARAM_EXTRACTOR] Calling extract_email_parameters for: {description[:50]}...")
            extracted = extract_email_parameters(description)
            args.update({
                "to": extracted.get("to", []),
                "subject": extracted.get("subject"),
                "body": extracted.get("body"),
            })
            logger.info(f"[PARAM_EXTRACTOR] Extracted email params: {args}")
        
        # Email draft
        elif tool_lower in ["email_draft", "gmail.create_draft", "create_draft"]:
            logger.info(f"[PARAM_EXTRACTOR] Calling extract_email_parameters for draft: {description[:50]}...")
            extracted = extract_email_parameters(description)
            args.update({
                "to": extracted.get("to", []),
                "subject": extracted.get("subject"),
                "body": extracted.get("body"),
            })
            logger.info(f"[PARAM_EXTRACTOR] Extracted draft params: {args}")
        
        # Email list/fetch - no special extraction needed
        elif tool_lower in ["email_list", "gmail.list_recent_threads", "list_recent_threads"]:
            args["max_results"] = 10  # Default
        
        # Fallback: try basic email extraction from description
        else:
            emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', description)
            if emails:
                args["to"] = emails
        
        return args
    
    def _sanitize_description(self, description: str) -> str:
        """Remove PII (emails, phone numbers) from description for privacy in logs."""
        # Remove email addresses
        description = re.sub(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            '[EMAIL]',
            description,
        )
        # Remove phone numbers (basic pattern)
        description = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE]', description)
        return description
    
    def _validate_dag(self, atoms: List[ExecutionAtom]) -> None:
        """Validate that the execution plan forms a valid DAG (no cycles)."""
        graph = nx.DiGraph()
        
        for atom in atoms:
            graph.add_node(atom.id)
            for dep in atom.dependencies:
                graph.add_edge(dep, atom.id)
        
        if not nx.is_directed_acyclic_graph(graph):
            raise ValueError("Invalid execution plan: cycle detected in dependencies")


# Singleton instance for import convenience
atomizer_engine = AtomizerEngine()
