from typing import Dict, Any, Optional
from app.agents.email.graph import email_graph

class EmailAgentService:
    
    async def handle_skill(
        self,
        skill_id: str,
        args: Dict[str, Any],
        user_id: str,
        tool_tokens: Optional[Dict],
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        
        print(f"🤖 Email Agent: Received skill '{skill_id}'")
        
        # 1. Prepare Initial State
        initial_state = {
            "user_id": user_id,
            "args": args,
            "tool_tokens": tool_tokens,
            "metadata": metadata, 
            "email_context": None,
            "generated_content": None,
            "final_result": {} # Starts empty
        }
        
        # 2. Run the Graph
        final_state = await email_graph.ainvoke(initial_state)
        
        # --- THE FIX ---
        # If 'final_result' is empty (because we skipped the Action Node),
        # but we successfully generated content (Summary/Triage), use that!
        result = final_state.get("final_result")
        if not result and final_state.get("generated_content"):
            print("ℹ️ Read-Only Skill detected. Returning generated content directly.")
            return {
                "status": "success",
                "content": final_state["generated_content"]
            }
            
        # 3. Return the result normally (for Drafts/Actions)
        return result or {"status": "error", "message": "No result generated"}

email_agent = EmailAgentService()