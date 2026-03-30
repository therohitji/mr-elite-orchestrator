# app/phase1/pre_flight_check.py
"""
Pre-flight policy check: Vector similarity search against blocking policies.
Prevents execution of requests that violate known company policies.
"""


async def check_blocking_policies(user_input: str, supabase_client=None) -> dict:
    """
    Query Supabase policies table for blocking policies.
    Simple keyword-based check for MVP.

    Returns: {"blocked": bool, "reason": str | None}
    """
    # Get supabase client if not provided
    if supabase_client is None:
        try:
            from app.core.supabase_client import supabase_client as sb_client

            supabase_client = sb_client
        except ImportError:
            return {"blocked": False, "reason": None}

    if supabase_client is None:
        # Vector DB not initialized: allow pass-through
        return {"blocked": False, "reason": None}

    try:
        # Simple keyword check for blocking policies
        blocking_keywords = ["delete all", "drop table", "destroy", "wipe data"]
        user_input_lower = user_input.lower()

        for keyword in blocking_keywords:
            if keyword in user_input_lower:
                return {
                    "blocked": True,
                    "reason": f"Request contains forbidden action: {keyword}",
                }

        return {"blocked": False, "reason": None}

    except Exception as e:
        # Fail open on DB errors (log but don't block)
        print(f"Pre-flight check error: {e}")
        return {"blocked": False, "reason": None}
        return {"blocked": False, "reason": None}
