import datetime
import json
from langgraph.graph import StateGraph, END
from app.agents.calendar.state import CalendarAgentState
from app.mr_elite.core.llm import llm_client
from app.tools.calendar import CalendarTool
from app.agents.calendar.models import CalendarPlan


# --- NODE 1: FETCH CONTEXT ---
async def context_fetcher(state: CalendarAgentState):
    print("🗓️ CalGraph: Fetching context...")
    metadata = state["metadata"]
    tool_actions = metadata.get("tool_actions", [])
    tokens = state["tool_tokens"]

    calendar = CalendarTool(tokens)
    context_data = ""

    if (
        "list_events" in tool_actions
        or "get_free_busy" in tool_actions
        or "update_event" in tool_actions
        or "delete_event" in tool_actions
    ):
        try:
            # 1. Get Timezone
            user_tz = await calendar.get_user_timezone()
            context_data = f"User Timezone: {user_tz}\n"

            # 2. FETCH 7 DAYS OF EVENTS (Weekly Context)
            # This ensures we can find events even if they are tomorrow/next week
            events_list = await calendar.list_events(date_range="week")
            context_data += events_list
        except Exception as e:
            context_data = f"Error fetching context: {e}"

    return {"calendar_context": context_data}


    # --- NODE 2: GENERATE (LLM) ---
async def content_generator(state: CalendarAgentState):
    print("🧠 CalGraph: Planning...")
    metadata = state["metadata"]
    skill_id = metadata.get("id", "")
    tool_actions = str(metadata.get("tool_actions", []))
    context = state.get("calendar_context", "")
    args = state["args"]
    
    # Extract chat history from metadata if available
    chat_history = metadata.get("chat_history", [])
    previous_atoms = metadata.get("previous_atoms", [])

    # Calculate Local Time (IST)
    try:
        ist_offset = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        now_local = datetime.datetime.now(ist_offset).strftime(
            "%Y-%m-%d %H:%M:%S (IST)"
        )
    except:
        now_local = datetime.datetime.now().isoformat()

    # --- 1. DEFAULT PROMPT (Create Event) ---
    system_instruction = """
    INSTRUCTIONS:
    1. Analyze the User Request.
    2. Convert relative times (e.g., "tomorrow at 4pm") into ISO 8601 format.
    3. If no end time is specified, add 30 minutes.
    
    SET 'action' to 'create'.
    POPULATE 'event' with:
       - title: Exact Title
       - start: ISO_DATE
       - end: ISO_DATE
       - attendees: ["email@example.com"]
    """

    # --- 2. AGENDA / LIST PROMPT ---
    if "agenda" in skill_id or "list_events" in tool_actions:
        system_instruction = """
        INSTRUCTIONS:
        1. Analyze the CALENDAR CONTEXT.
        2. Identify events matching the requested date.
        
        SET 'action' to 'list'.
        POPULATE 'agenda_summary' with a list of found events:
           - id: EXACT_ID_FROM_CONTEXT
           - title: Meeting Title
           - start: ISO_DATE
           - status: confirmed
        """

    # --- 3. SUGGEST SLOTS PROMPT ---
    elif "suggest" in skill_id or "get_free_busy" in tool_actions:
        system_instruction = """
        INSTRUCTIONS:
        1. Analyze the CALENDAR CONTEXT.
        2. Find gaps where the user is FREE.
        
        SET 'action' to 'suggest'.
        POPULATE 'suggested_slots' with a list of available times:
             - { "start": "ISO_DATE", "end": "ISO_DATE" }
        """

    # --- 4. RESCHEDULE / UPDATE / CANCEL PROMPT ---
    elif (
        "reschedule" in skill_id
        or "update_event" in tool_actions
        or "delete_event" in tool_actions
    ):
        system_instruction = """
        INSTRUCTIONS:
        1. IDENTIFY THE EVENT ID:
           - Use the ID if provided, otherwise find it in context by matching title.

        2. DETERMINE THE ACTION (Set "action" field):
           - "delete" for cancelling/removing.
           - "update" for rescheduling/renaming.

        3. SET VARIABLES:
           - For update: set 'event.title', 'event.start', 'event.end' as needed.
           - For delete: set 'event.id'.
        """

    # Build chat history context if available
    chat_context_str = ""
    if chat_history:
        chat_context_str = "\n\nCONVERSATION HISTORY:\n"
        # Pass full chat history - caller controls limits
        for msg in chat_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            # Truncate very long messages only if needed (keep last 400 chars)
            if len(content) > 400:
                content = content[-400:] + " [truncated]"
            chat_context_str += f"  {role}: {content}\n"
    
    if previous_atoms:
        chat_context_str += "\nPREVIOUS ACTIONS:\n"
        # Pass all previous atoms for context
        for atom in previous_atoms:
            desc = atom.get("description", "")
            status = atom.get("status", "")
            chat_context_str += f"  - {desc} [{status}]\n"

    prompt = f"""
    SYSTEM: You are a Calendar Assistant.
    CURRENT USER TIME: {now_local}
    
    CONTEXT (Upcoming 7 Days):
    {context}
    {chat_context_str}
    
    USER REQUEST:
    {args}
    
    {system_instruction}
    """

    # CALL STRUCTURED LLM
    response: CalendarPlan = await llm_client.get_structured_response(prompt, CalendarPlan)
    
    # Store the DICT representation for the state
    return {"generated_content": response.model_dump() if response else {}}


# --- NODE 3: ACTION (EXECUTE) ---
async def action_executor(state: CalendarAgentState):
    metadata = state["metadata"]
    tool_actions = metadata.get("tool_actions", [])

    # 1. PARSE LLM OUTPUT
    # Now it's ALREADY a dictionary (from .model_dump())
    parsed_content = state["generated_content"]
    
    if not parsed_content:
         return {
            "final_result": {"status": "error", "message": "Failed to generate LLM plan."}
        }

    thought = parsed_content.get("thought_process", "No reasoning provided.")
    print(f"🧠 CalGraph Thought: {thought}")
    
    # 2. DETERMINE ACTION
    # Now we rely on the strict 'action' field or fallback to tools
    llm_action = parsed_content.get("action", "none").lower()

    tokens = state["tool_tokens"]
    args = state["args"]
    calendar = CalendarTool(tokens)
    result = {"status": "success", "content": parsed_content} # Return STRUCTURAL content

    # Helper to safely get data
    # The 'event' field is now a dict (nested inside parsed_content)
    root_event = parsed_content.get("event") or {}
    
    def get_val(key):
        return root_event.get(key) or args.get(key)

    # --- A. CREATE EVENT ---
    if "create_event" in tool_actions and llm_action != "update" and llm_action != "delete":
        title = get_val("title") or "New Meeting"
        start = get_val("start") or args.get("start_time")
        end = get_val("end")
        attendees = get_val("attendees") or []

        print(f"⚡ CalGraph: Creating '{title}'...")
        if start:
            res = await calendar.create_event(title, start, end, attendees)
            result["action_result"] = res
        else:
            result["action_result"] = "Error: Start time missing."

    # --- B. DELETE EVENT ---
    # Trigger if LLM said "delete" OR if delete is the only tool
    elif llm_action == "delete" or ("delete_event" in tool_actions and "update_event" not in tool_actions):
        event_id = get_val("id")
        print(f"⚡ CalGraph: Deleting Event ID {event_id}...")

        if event_id:
            res = await calendar.delete_event(event_id)
            result["action_result"] = res
        else:
            result["action_result"] = "Error: Could not find Event ID to delete."

    # --- C. UPDATE EVENT ---
    # Trigger if LLM said "update" OR if update is available
    elif llm_action == "update" or "update_event" in tool_actions:
        event_id = get_val("id")
        new_title = get_val("title")
        new_start = get_val("start")
        new_end = get_val("end")

        print(f"⚡ CalGraph: Updating Event ID: {event_id}")
        print(f"   Values -> Title: {new_title} | Start: {new_start}")

        if event_id:
            res = await calendar.update_event(
                event_id, new_start=new_start, new_end=new_end, new_title=new_title
            )
            result["action_result"] = res
        else:
            result["action_result"] = "Error: Could not find Event ID to update."

    return {"final_result": result}


# --- GRAPH SETUP ---
workflow = StateGraph(CalendarAgentState)
workflow.add_node("fetch_context", context_fetcher)
workflow.add_node("generate_content", content_generator)
workflow.add_node("execute_action", action_executor)

workflow.set_entry_point("fetch_context")


def should_execute(state):
    actions = state["metadata"].get("tool_actions", [])
    if (
        "create_event" in actions
        or "update_event" in actions
        or "delete_event" in actions
    ):
        return "execute_action"
    return END


workflow.add_edge("fetch_context", "generate_content")
workflow.add_conditional_edges("generate_content", should_execute)
workflow.add_edge("execute_action", END)

calendar_graph = workflow.compile()