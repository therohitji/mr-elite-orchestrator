# app/agents/email/graph.py
from langgraph.graph import StateGraph, END
from app.agents.email.state import EmailAgentState
from app.mr_elite.core.llm import llm_client
from app.tools.gmail import GmailTool  # <--- Import Real Tool
from app.agents.email.models import EmailPlan


# --- NODE 1: FETCH CONTEXT ---
async def context_fetcher(state: EmailAgentState):
    print("🔄 Graph: Fetching email data...")
    metadata = state["metadata"]
    tool_actions = metadata.get("tool_actions", [])
    tokens = state["tool_tokens"]
    args = state["args"]

    gmail = GmailTool(tokens)
    context_data = ""

    # --- LOGIC FIX ---
    # Check if we have a specific ID first.
    # If we have an ID AND the skill allows fetching, do that.
    thread_ref = args.get("thread_reference")

    if "fetch_thread" in tool_actions and thread_ref:
        print(f"📧 Graph: Fetching specific thread {thread_ref}...")
        context_data = await gmail.get_thread(thread_ref)

    # If no ID was provided, BUT we are allowed to list threads (Triage mode)
    elif "list_recent_threads" in tool_actions:
        print("📧 Graph: No ID provided. Defaulting to 'list_recent_threads'...")
        context_data = await gmail.list_threads(limit=5)

    return {"email_context": context_data}


# --- NODE 2: GENERATE (LLM) ---
async def content_generator(state: EmailAgentState):
    print("🧠 Graph: Thinking...")
    metadata = state["metadata"]
    skill_id = metadata.get("id", "")
    tool_actions = str(metadata.get("tool_actions", []))
    context = state.get("email_context", "")
    args = state["args"]
    description = metadata.get("description", "You are an Email Assistant.")

    prompt = f"""
    ROLE: {description}
    
    EMAIL DATA (If any):
    {context}
    
    USER REQUEST:
    {args}
    
    ORIGINAL MESSAGE (Use this to find missing details like Email Address):
    "{args.get('_original_message', '')}"
    
    INSTRUCTIONS:
    1. Analyze the context and request.
    2. Decide on the 'action':
       - 'send': If the user explicitly asks to SEND (e.g. "Send email", "Mail to").
       - 'draft': If asking to DRAFT or COMPOSE (e.g. "Draft", "Create", "Prepare").
       - 'reply': If responding to an existing thread.
       - 'list_threads': If asking for a summary.
       
    3. If sending/drafting/replying, populate the 'email' payload:
       - to: The RECIPIENT EMAIL ADDRESS (e.g. 'foo@bar.com'). DO NOT USE A NAME. Extract the email from the request if possible.
       - subject: Title
       - body: The content
       - thread_reference: The ID if replying
    """

    # Using 'get_structured_response' for strict typing
    response: EmailPlan = await llm_client.get_structured_response(prompt, EmailPlan)
    
    return {"generated_content": response.model_dump() if response else {}}


# --- NODE 3: ACTION (WRITE) ---
async def action_executor(state: EmailAgentState):
    metadata = state["metadata"]
    tool_actions = metadata.get("tool_actions", [])
    
    # Already a dict
    parsed_content = state["generated_content"]
    if not parsed_content:
         return {
            "final_result": {"status": "error", "message": "Failed to generate email plan."}
        }
    
    tokens = state["tool_tokens"]
    gmail = GmailTool(tokens)
    
    # Check what the LLM decided to do
    llm_action = parsed_content.get("action", "none")
    email_payload = parsed_content.get("email") or {}
    
    # Fallback to arguments if LLM missed something (simple merging)
    def get_val(key):
        return email_payload.get(key) or state["args"].get(key)

    result = {"status": "success", "content": parsed_content}

    # --- ACTION: SEND EMAIL ---
    if "send_email" in tool_actions and (llm_action == "send" or llm_action == "reply"):
        print(f"📧 Graph: Sending Email to {get_val('to')}...")
        try:
            res = await gmail.send_email(
                to=get_val("to"),
                subject=get_val("subject"),
                body=get_val("body"),
                thread_id=get_val("thread_reference")
            )
            result["action_result"] = res
        except Exception as e:
            result["action_result"] = f"Error sending email: {e}"

    # --- ACTION: CREATE DRAFT ---
    elif "create_draft" in tool_actions and llm_action == "draft":
        print("✍️ Graph: Creating Draft...")
        try:
            res = await gmail.create_draft(
                to=get_val("to"),
                subject=get_val("subject"),
                body=get_val("body"),
                thread_id=get_val("thread_reference"),
            )
            result["action_result"] = res
        except Exception as e:
            result["action_result"] = f"Error creating draft: {e}"

    return {"final_result": result}


# --- GRAPH SETUP ---
workflow = StateGraph(EmailAgentState)
workflow.add_node("fetch_context", context_fetcher)
workflow.add_node("generate_content", content_generator)
workflow.add_node("execute_action", action_executor)

workflow.set_entry_point("fetch_context")


# Conditional Logic: Do we need to Write?
def should_execute(state):
    actions = state["metadata"].get("tool_actions", [])
    if "create_draft" in actions or "send_email" in actions:
        return "execute_action"
    return END


workflow.add_edge("fetch_context", "generate_content")
workflow.add_conditional_edges("generate_content", should_execute)
workflow.add_edge("execute_action", END)

email_graph = workflow.compile()
