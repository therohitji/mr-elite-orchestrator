# app/agents/calendar/service.py
from typing import Dict, Any, Optional
from app.agents.calendar.graph import calendar_graph


class CalendarAgentService:

    async def handle_skill(
        self,
        skill_id: str,
        args: Dict[str, Any],
        user_id: str,
        tool_tokens: Optional[Dict],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:

        print(f"🗓️ Calendar Agent: Received skill '{skill_id}'")

        initial_state = {
            "user_id": user_id,
            "args": args,
            "tool_tokens": tool_tokens,
            "metadata": metadata,
            "calendar_context": None,
            "generated_content": None,
            "final_result": {},
        }

        # Run Graph
        final_state = await calendar_graph.ainvoke(initial_state)

        # Handle Read-Only Skills (e.g., Agenda Summary)
        result = final_state.get("final_result")
        if not result and final_state.get("generated_content"):
            return {"status": "success", "content": final_state["generated_content"]}

        return result or {"status": "error", "message": "No result generated"}


calendar_agent = CalendarAgentService()
