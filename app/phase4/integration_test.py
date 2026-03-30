"""
Phase 4 Integration Test: Complete workflow demonstration
Tests the flow from atoms -> dispatcher -> agents -> tools
"""

import asyncio
import logging
from uuid import uuid4
from typing import Dict, Any

from app.phase1.atoms import ExecutionAtom
from app.phase4.orchestrator import ExecutionOrchestrator
from app.phase4.dispatcher import CentralDispatcher, ExecutionStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_email_tool_integration():
    """
    Test email tool integration through the complete Phase 4 flow.
    """
    print("\n" + "=" * 80)
    print("PHASE 4 EMAIL TOOL INTEGRATION TEST")
    print("=" * 80 + "\n")

    # Setup
    org_id = "org_test"
    workspace_id = "ws_test"
    request_id = f"req_{uuid4()}"
    user_id = "test_user"

    print(f"📋 Setup:")
    print(f"   Organization ID: {org_id}")
    print(f"   Workspace ID: {workspace_id}")
    print(f"   Request ID: {request_id}")
    print(f"   User ID: {user_id}\n")

    # Create dispatcher
    dispatcher = CentralDispatcher(org_id, workspace_id, request_id)

    # Test 1: List Threads
    print("TEST 1: List Email Threads")
    print("-" * 80)

    atom1 = ExecutionAtom(
        id=uuid4(),
        tool_name="list_threads",
        tool_arguments={"limit": 5},
        context_data={"user_id": user_id},
        output_variable="email_threads",
    )

    print(f"Atom ID: {atom1.id}")
    print(f"Tool: {atom1.tool_name}")
    print(f"Arguments: {atom1.tool_arguments}")
    print(f"Context: {atom1.context_data}\n")

    try:
        print("🚀 Dispatching atom...")
        result1 = await dispatcher.dispatch(atom1)

        print(f"✅ Status: {result1.status.value}")
        print(f"📊 Duration: {result1.duration_ms:.2f}ms")
        print(f"📍 Trace ID: {result1.trace_id}")
        print(f"📤 Output: {result1.output}\n")
    except Exception as e:
        print(f"❌ Error: {str(e)}\n")

    # Test 2: Send Email
    print("\nTEST 2: Send Email")
    print("-" * 80)

    atom2 = ExecutionAtom(
        id=uuid4(),
        tool_name="send_email",
        tool_arguments={
            "to": "recipient@example.com",
            "subject": "Test Email",
            "body": "This is a test email from Phase 4 integration test.",
        },
        context_data={"user_id": user_id},
        output_variable="email_send_result",
    )

    print(f"Atom ID: {atom2.id}")
    print(f"Tool: {atom2.tool_name}")
    print(f"Arguments: {atom2.tool_arguments}")
    print(f"Context: {atom2.context_data}\n")

    try:
        print("🚀 Dispatching atom...")
        result2 = await dispatcher.dispatch(atom2)

        print(f"✅ Status: {result2.status.value}")
        print(f"📊 Duration: {result2.duration_ms:.2f}ms")
        print(f"📍 Trace ID: {result2.trace_id}")
        print(f"📤 Output: {result2.output}\n")
    except Exception as e:
        print(f"❌ Error: {str(e)}\n")

    # Test 3: Get Specific Thread
    print("\nTEST 3: Get Specific Thread")
    print("-" * 80)

    atom3 = ExecutionAtom(
        id=uuid4(),
        tool_name="get_thread",
        tool_arguments={"thread_id": "thread_123"},
        context_data={"user_id": user_id},
        output_variable="thread_content",
    )

    print(f"Atom ID: {atom3.id}")
    print(f"Tool: {atom3.tool_name}")
    print(f"Arguments: {atom3.tool_arguments}")
    print(f"Context: {atom3.context_data}\n")

    try:
        print("🚀 Dispatching atom...")
        result3 = await dispatcher.dispatch(atom3)

        print(f"✅ Status: {result3.status.value}")
        print(f"📊 Duration: {result3.duration_ms:.2f}ms")
        print(f"📍 Trace ID: {result3.trace_id}")
        print(f"📤 Output: {result3.output}\n")
    except Exception as e:
        print(f"❌ Error: {str(e)}\n")

    # Test 4: Create Draft
    print("\nTEST 4: Create Email Draft")
    print("-" * 80)

    atom4 = ExecutionAtom(
        id=uuid4(),
        tool_name="create_draft",
        tool_arguments={
            "to": "recipient@example.com",
            "subject": "Draft Email",
            "body": "This is a draft email.",
        },
        context_data={"user_id": user_id},
        output_variable="draft_result",
    )

    print(f"Atom ID: {atom4.id}")
    print(f"Tool: {atom4.tool_name}")
    print(f"Arguments: {atom4.tool_arguments}")
    print(f"Context: {atom4.context_data}\n")

    try:
        print("🚀 Dispatching atom...")
        result4 = await dispatcher.dispatch(atom4)

        print(f"✅ Status: {result4.status.value}")
        print(f"📊 Duration: {result4.duration_ms:.2f}ms")
        print(f"📍 Trace ID: {result4.trace_id}")
        print(f"📤 Output: {result4.output}\n")
    except Exception as e:
        print(f"❌ Error: {str(e)}\n")

    print("\n" + "=" * 80)
    print("TEST SUITE COMPLETED")
    print("=" * 80)


async def test_calendar_tool_integration():
    """
    Test calendar tool integration (placeholder).
    """
    print("\n" + "=" * 80)
    print("PHASE 4 CALENDAR TOOL INTEGRATION TEST")
    print("=" * 80 + "\n")

    org_id = "org_test"
    workspace_id = "ws_test"
    request_id = f"req_{uuid4()}"
    user_id = "test_user"

    dispatcher = CentralDispatcher(org_id, workspace_id, request_id)

    # Test: Create Event
    print("TEST: Create Calendar Event")
    print("-" * 80)

    atom = ExecutionAtom(
        id=uuid4(),
        tool_name="create_event",
        tool_arguments={
            "title": "Team Meeting",
            "start_time": "2026-01-15T10:00:00",
            "end_time": "2026-01-15T11:00:00",
            "description": "Regular team sync",
        },
        context_data={"user_id": user_id},
        output_variable="event_result",
    )

    print(f"Atom ID: {atom.id}")
    print(f"Tool: {atom.tool_name}")
    print(f"Arguments: {atom.tool_arguments}\n")

    try:
        print("🚀 Dispatching atom...")
        result = await dispatcher.dispatch(atom)

        print(f"✅ Status: {result.status.value}")
        print(f"📤 Output: {result.output}\n")
    except Exception as e:
        print(f"❌ Error: {str(e)}\n")


if __name__ == "__main__":
    print("\n🔥 Starting Phase 4 Integration Tests...\n")

    asyncio.run(test_email_tool_integration())
    asyncio.run(test_calendar_tool_integration())

    print("\n✅ All tests completed!\n")
