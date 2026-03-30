# app/phase1/workspace_cache.py
"""
Workspace Cache Manager - Caches agents and tools for each workspace.
Prevents repeated Supabase queries on every user request.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class WorkspaceCache:
    """
    Singleton cache for workspace agents and tools.
    Loads once per workspace and refreshes periodically.
    """

    _instance = None
    _cache: Dict[str, Dict[str, Any]] = {}  # workspace_id -> {agents, tools, timestamp}
    _refresh_interval = timedelta(minutes=30)  # Refresh every 30 minutes

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _is_cache_valid(self, workspace_id: str) -> bool:
        """Check if cache exists and is not expired."""
        if workspace_id not in self._cache:
            return False
        
        cached = self._cache[workspace_id]
        if "timestamp" not in cached:
            return False
        
        age = datetime.utcnow() - cached["timestamp"]
        return age < self._refresh_interval

    async def get_agents_and_tools(
        self, 
        workspace_id: str, 
        supabase_client=None,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Get agents and tools for a workspace (cached).
        
        Returns:
            {
                "agents": [{"name": "Gmail Agent", "description": "...", "tools": [...]}],
                "agents_lookup": {"gmail agent": {...}},  # lowercase name -> agent
                "tools_lookup": {"send_email": {...}},    # code -> tool
            }
        """
        # Check if cache is valid
        if not force_refresh and self._is_cache_valid(workspace_id):
            logger.info(f"[WORKSPACE CACHE] Using cached data for {workspace_id}")
            return self._cache[workspace_id]

        # Load from Supabase
        logger.info(f"[WORKSPACE CACHE] Loading agents/tools for {workspace_id} from Supabase...")
        data = await self._load_from_supabase(workspace_id, supabase_client)

        # Cache the result
        self._cache[workspace_id] = data
        self._cache[workspace_id]["timestamp"] = datetime.utcnow()

        logger.info(
            f"[WORKSPACE CACHE] Cached {len(data.get('agents', []))} agents and "
            f"{len(data.get('tools_lookup', {}))} tools for {workspace_id}"
        )

        return data

    async def _load_from_supabase(
        self, 
        workspace_id: str, 
        supabase_client=None
    ) -> Dict[str, Any]:
        """Load agents and tools from Supabase."""
        if supabase_client is None:
            try:
                from app.core.supabase_client import supabase_client as sb_client
                supabase_client = sb_client
            except ImportError:
                logger.warning("Supabase client not available, using fallback")
                return self._get_fallback_data()

        if supabase_client is None:
            return self._get_fallback_data()

        try:
            # Get workspace access configuration
            workspace_result = (
                supabase_client.table("workspaces")
                .select("agents_tools_access")
                .eq("id", workspace_id)
                .single()
                .execute()
            )

            if not workspace_result.data:
                logger.warning(f"[WORKSPACE CACHE] Workspace {workspace_id} not found")
                return self._get_fallback_data()

            agents_tools_access = workspace_result.data.get("agents_tools_access", {})

            if not agents_tools_access:
                logger.warning(f"[WORKSPACE CACHE] No agents_tools_access for workspace {workspace_id}")
                return self._get_fallback_data()

            agents_with_tools = []
            all_tools = {}

            for agent_id, tool_ids in agents_tools_access.items():
                # Fetch agent details
                agent_result = (
                    supabase_client.table("agents")
                    .select("id, name, description, metadata")
                    .eq("id", agent_id)
                    .eq("status", "ACTIVE")
                    .single()
                    .execute()
                )

                if not agent_result.data:
                    logger.warning(f"[WORKSPACE CACHE] Agent {agent_id} not found or inactive")
                    continue

                agent_data = agent_result.data

                # Fetch tools for this agent
                tools = []
                if tool_ids:
                    tools_result = (
                        supabase_client.table("tools")
                        .select("id, name, code, connector, operation, risk_level, requires_approval, description")
                        .in_("id", tool_ids)
                        .eq("status", "ACTIVE")
                        .execute()
                    )

                    if tools_result.data:
                        for t in tools_result.data:
                            tool_info = {
                                "id": t.get("id", ""),
                                "name": t.get("name", ""),
                                "code": t.get("code", ""),
                                "connector": t.get("connector", ""),
                                "operation": t.get("operation", ""),
                                "risk_level": t.get("risk_level", "MEDIUM"),
                                "requires_approval": t.get("requires_approval", False),
                                "description": t.get("description", ""),
                            }
                            tools.append(tool_info)
                            # Add to lookup by code (lowercase)
                            all_tools[t.get("code", "").lower()] = tool_info

                agents_with_tools.append({
                    "id": agent_data.get("id", ""),
                    "name": agent_data.get("name", "Unknown Agent"),
                    "description": agent_data.get("description", ""),
                    "metadata": agent_data.get("metadata", {}),
                    "tools": tools,
                })

            # Build agent lookup (by lowercase name)
            agents_lookup = {a["name"].lower(): a for a in agents_with_tools}

            return {
                "agents": agents_with_tools,
                "agents_lookup": agents_lookup,
                "tools_lookup": all_tools,
            }

        except Exception as e:
            logger.error(f"[WORKSPACE CACHE] Error loading from Supabase: {e}")
            return self._get_fallback_data()

    def _get_fallback_data(self) -> Dict[str, Any]:
        """Fallback data if Supabase is unavailable."""
        logger.warning("[WORKSPACE CACHE] Using fallback data")
        
        fallback_agents = [
            {
                "id": "fb-gmail",
                "name": "Gmail Agent",
                "description": "Handles email operations",
                "tools": [
                    {"id": "fb-1", "name": "Send Email", "code": "send_email", "connector": "gmail", "operation": "send", "risk_level": "MEDIUM", "description": "Send an email"},
                    {"id": "fb-2", "name": "Create Draft", "code": "email_draft", "connector": "gmail", "operation": "create_draft", "risk_level": "LOW", "description": "Create email draft"},
                    {"id": "fb-3", "name": "List Emails", "code": "email_list", "connector": "gmail", "operation": "list", "risk_level": "LOW", "description": "List email threads"},
                    {"id": "fb-4", "name": "Read Email", "code": "email_read", "connector": "gmail", "operation": "get", "risk_level": "LOW", "description": "Read email thread"},
                ],
            },
            {
                "id": "fb-calendar",
                "name": "Calendar Agent",
                "description": "Handles calendar operations",
                "tools": [
                    {"id": "fb-5", "name": "Create Event", "code": "event_create", "connector": "calendar", "operation": "create", "risk_level": "MEDIUM", "description": "Create calendar event"},
                    {"id": "fb-6", "name": "List Events", "code": "event_list", "connector": "calendar", "operation": "list", "risk_level": "LOW", "description": "List calendar events"},
                    {"id": "fb-7", "name": "Update Event", "code": "event_update", "connector": "calendar", "operation": "update", "risk_level": "MEDIUM", "description": "Update calendar event"},
                    {"id": "fb-8", "name": "Delete Event", "code": "event_delete", "connector": "calendar", "operation": "delete", "risk_level": "HIGH", "description": "Delete calendar event"},
                ],
            },
            {
                "id": "fb-summarizer",
                "name": "Summarizer Agent",
                "description": "Summarizes content",
                "tools": [
                    {"id": "fb-9", "name": "Summarize Text", "code": "summarize_text", "connector": "summarizer", "operation": "summarize", "risk_level": "LOW", "description": "Summarize text"},
                    {"id": "fb-10", "name": "Summarize Emails", "code": "summarize_emails", "connector": "summarizer", "operation": "summarize_emails", "risk_level": "LOW", "description": "Summarize emails"},
                ],
            },
            {
                "id": "fb-system",
                "name": "System",
                "description": "System utilities",
                "tools": [
                    {"id": "fb-11", "name": "Human Input", "code": "human_input", "connector": "system", "operation": "input", "risk_level": "LOW", "description": "Ask user for clarification"},
                ],
            },
        ]

        # Build lookups
        agents_lookup = {a["name"].lower(): a for a in fallback_agents}
        tools_lookup = {}
        for agent in fallback_agents:
            for tool in agent.get("tools", []):
                tools_lookup[tool["code"].lower()] = tool

        return {
            "agents": fallback_agents,
            "agents_lookup": agents_lookup,
            "tools_lookup": tools_lookup,
        }

    def invalidate(self, workspace_id: str = None):
        """Invalidate cache for a specific workspace or all workspaces."""
        if workspace_id:
            if workspace_id in self._cache:
                del self._cache[workspace_id]
                logger.info(f"[WORKSPACE CACHE] Invalidated cache for {workspace_id}")
        else:
            self._cache.clear()
            logger.info("[WORKSPACE CACHE] Invalidated all cache")


# Singleton instance
workspace_cache = WorkspaceCache()
