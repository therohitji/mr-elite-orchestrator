import os
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime
from uuid import UUID

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print(f"✅ Supabase client initialized (anon): {SUPABASE_URL}")
else:
    supabase_client = None
    print("⚠️  Supabase credentials not configured")

# Service role client bypasses RLS policies (for server-side operations)
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase_service_client: Client = create_client(
        SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
    )
    print(f"✅ Supabase service client initialized (bypasses RLS): {SUPABASE_URL}")
else:
    supabase_service_client = None
    print(
        "⚠️  Supabase service role credentials not configured (RLS bypass unavailable)"
    )


# UUID validation helper
DEFAULT_UUID = "00000000-0000-0000-0000-000000000000"


def ensure_valid_uuid(value: str) -> str:
    """Ensure value is a valid UUID, return default if not."""
    if not value or value == "default":
        return DEFAULT_UUID
    try:
        UUID(value)
        return value
    except (ValueError, AttributeError):
        return DEFAULT_UUID


def init_vector_db():
    """Check vector DB connection."""
    if not supabase_client:
        print("⚠️  Supabase not initialized")
        return

    try:
        result = (
            supabase_client.table("policy_rules")
            .select("count", count="exact")
            .limit(1)
            .execute()
        )
        print(f"✅ Vector DB connected (policy_rules table exists)")
    except Exception as e:
        print(f"⚠️  Vector DB error: {e}")


async def query_policies_vector(
    workspace_id: str, embedding: list, threshold: float = 0.5, limit: int = 5
) -> list:
    """Query policies via vector similarity using match_policies RPC."""
    db = supabase_service_client or supabase_client
    if not db:
        return []

    try:
        results = db.rpc(
            "match_policies",
            {
                "p_query_embedding": embedding,
                "p_workspace_id": ensure_valid_uuid(workspace_id),
                "p_match_threshold": threshold,
                "p_match_count": limit,
                "p_policy_types": ["blocking", "approval_required", "guideline"],
                "p_domain": None,
                "p_risk_scope": None,
            },
        ).execute()
        return results.data or []
    except Exception as e:
        print(f"⚠️  Policy query error: {e}")
        return []


async def query_resources_vector(
    workspace_id: str,
    embedding: list,
    threshold: float = 0.75,
    limit: int = 5,
    is_template: bool = False,
) -> list:
    """Query resources via vector similarity using match_resources RPC."""
    if not supabase_client:
        return []

    try:
        results = supabase_client.rpc(
            "match_resources",
            {
                "p_query_embedding": embedding,
                "p_workspace_id": ensure_valid_uuid(workspace_id),
                "p_threshold": threshold,
                "p_top_k": limit,
                "p_is_template": is_template,
                "p_category": None,
            },
        ).execute()

        return results.data or []
    except Exception as e:
        print(f"⚠️  Resource query error: {e}")
        return []


async def query_resource_chunks_vector(
    workspace_id: str, embedding: list, threshold: float = 0.5, limit: int = 5
) -> list:
    """Query resource chunks via vector similarity using match_resource_chunks RPC."""
    db = supabase_service_client or supabase_client
    if not db:
        return []

    try:
        results = db.rpc(
            "match_resource_chunks",
            {
                "p_query_embedding": embedding,
                "p_workspace_id": ensure_valid_uuid(workspace_id),
                "p_threshold": threshold,
                "p_top_k": limit,
                "p_category": None,
                "p_resource_id": None,
            },
        ).execute()
        return results.data or []
    except Exception as e:
        print(f"⚠️  Resource chunk query error: {e}")
        return []


async def log_policy_decision(
    workspace_id: str,
    atom_id: str,
    policy_id: str,
    decision: str,
    confidence: float,
    details: str = None,
) -> dict:
    """Log policy decision to audit trail."""
    if not supabase_client:
        return {"error": "Supabase not initialized"}

    try:
        result = (
            supabase_client.table("policy_decisions")
            .insert(
                {
                    "workspace_id": workspace_id,
                    "atom_id": atom_id,
                    "policy_id": policy_id,
                    "decision": decision,  # GREEN, YELLOW, RED
                    "confidence": confidence,
                    "details": details,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            .execute()
        )

        return (
            {"id": result.data[0]["id"], "status": "logged"}
            if result.data
            else {"status": "logged"}
        )

    except Exception as e:
        print(f"⚠️  Policy decision log error: {e}")
        return {"error": str(e)}


async def log_resource_access(
    workspace_id: str,
    atom_id: str,
    resource_id: str,
    resource_type: str,
    retrieved: bool = True,
    source: str = None,
) -> dict:
    """Log resource access to audit trail."""
    if not supabase_client:
        return {"error": "Supabase not initialized"}

    try:
        result = (
            supabase_client.table("resource_access_logs")
            .insert(
                {
                    "workspace_id": workspace_id,
                    "atom_id": atom_id,
                    "resource_id": resource_id,
                    "resource_type": resource_type,  # template, knowledge, reference
                    "retrieved": retrieved,
                    "source": source,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            .execute()
        )

        return (
            {"id": result.data[0]["id"], "status": "logged"}
            if result.data
            else {"status": "logged"}
        )

    except Exception as e:
        print(f"⚠️  Resource access log error: {e}")
        return {"error": str(e)}


async def insert_policy(
    rule_text: str, policy_type: str, workspace_id: str, embedding: list = None
) -> dict:
    """Insert policy with embedding."""
    if not supabase_client:
        return {"error": "Supabase not initialized"}

    try:
        if embedding is None:
            from app.core.gemini_client import call_gemini_embedding

            embedding = await call_gemini_embedding(rule_text)

        result = (
            supabase_client.table("policy_rules")
            .insert(
                {
                    "policy_type": policy_type,
                    "rule_text": rule_text,
                    "embedding": embedding,
                    "workspace_id": workspace_id,
                }
            )
            .execute()
        )

        return {"id": result.data[0]["id"], "status": "inserted"}

    except Exception as e:
        print(f"⚠️  Policy insert error: {e}")
        return {"error": str(e)}


# ============================================================================
# MEMORY LAYER FUNCTIONS
# ============================================================================


async def match_memories_rpc(
    org_id: str,
    query_embedding: list,
    memory_type: str = None,
    entity_type: str = None,
    entity_id: str = None,
    limit: int = 5,
    threshold: float = 0.5,
) -> list:
    """Query memories via semantic search using match_memories RPC."""
    if not supabase_client:
        return []

    try:
        params = {
            "p_org_id": org_id,
            "p_query_embedding": query_embedding,
            "p_k": limit,
            "p_min_similarity": threshold,
            "p_types": ["episode", "semantic", "preference", "entity_note"],
            "p_workspace_id": None,
        }

        if entity_type:
            params["p_entity_type"] = entity_type
        if entity_id:
            params["p_entity_id"] = entity_id

        result = supabase_client.rpc("match_memories", params).execute()
        return result.data or []

    except Exception as e:
        print(f"⚠️  Memory search error: {e}")
        return []


async def get_entity_timeline_rpc(
    org_id: str,
    entity_type: str,
    entity_id: str,
    limit: int = 20,
) -> list:
    """Get entity timeline via RPC."""
    if not supabase_client:
        return []

    try:
        result = supabase_client.rpc(
            "get_entity_timeline",
            {
                "p_org_id": org_id,
                "p_entity_type": entity_type,
                "p_entity_id": entity_id,
                "p_limit": limit,
            },
        ).execute()

        return result.data or []

    except Exception as e:
        print(f"⚠️  Entity timeline error: {e}")
        return []


async def save_memory_item(
    org_id: str,
    workspace_id: str,
    memory_type: str,
    title: str,
    content: str,
    source: str = "workflow",
    entity_type: str = None,
    entity_id: str = None,
    importance: int = 50,
    created_by: str = None,
) -> dict:
    """Insert memory item into database."""
    if not supabase_client:
        return {"error": "Supabase not initialized"}

    try:
        # Validate and sanitize created_by
        # If it's a MongoDB ObjectId (24 char hex) or invalid UUID, set to None
        sanitized_created_by = None
        if created_by:
            try:
                # Check if it's a valid UUID
                from uuid import UUID

                UUID(created_by)
                sanitized_created_by = created_by
            except (ValueError, AttributeError):
                # Not a valid UUID, likely MongoDB ObjectId
                print(f"⚠️  created_by '{created_by}' is not a valid UUID, skipping")
                sanitized_created_by = None

        result = (
            supabase_client.table("memory_items")
            .insert(
                {
                    "org_id": org_id,
                    "workspace_id": workspace_id,
                    "created_by": sanitized_created_by,
                    "memory_type": memory_type,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "title": title,
                    "content": content,
                    "source": source,
                    "importance": importance,
                    "status": "active",
                }
            )
            .execute()
        )

        if result.data:
            return {"id": result.data[0]["id"], "status": "success"}
        else:
            return {"error": "Failed to insert memory"}

    except Exception as e:
        print(f"⚠️  Save memory error: {e}")
        return {"error": str(e)}


async def save_memory_vector(
    memory_id: str,
    org_id: str,
    embedding: list,
    model: str = "text-embedding-3-small",
) -> dict:
    """Save memory vector embedding."""
    if not supabase_client:
        return {"error": "Supabase not initialized"}

    try:
        result = (
            supabase_client.table("memory_vectors")
            .insert(
                {
                    "memory_id": memory_id,
                    "org_id": org_id,
                    "embedding": embedding,
                    "embedding_model": model,
                }
            )
            .execute()
        )

        if result.data:
            return {"status": "success"}
        else:
            return {"error": "Failed to save embedding"}

    except Exception as e:
        print(f"⚠️  Save embedding error: {e}")
        return {"error": str(e)}


async def save_workflow_state(
    org_id: str,
    workspace_id: str,
    workflow_id: str,
    thread_id: str,
    state: dict,
    step: str,
    status: str = "running",
    error_message: str = None,
) -> dict:
    """Save workflow execution state for resumption."""
    if not supabase_client:
        return {"error": "Supabase not initialized"}

    try:
        # Check if state already exists
        existing = (
            supabase_client.table("workflow_states")
            .select("id")
            .eq("org_id", org_id)
            .eq("workflow_id", workflow_id)
            .eq("thread_id", thread_id)
            .execute()
        )

        if existing.data and len(existing.data) > 0:
            # Update existing
            result = (
                supabase_client.table("workflow_states")
                .update(
                    {
                        "state": state,
                        "step": step,
                        "status": status,
                        "error_message": error_message,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                )
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            # Insert new
            result = (
                supabase_client.table("workflow_states")
                .insert(
                    {
                        "org_id": org_id,
                        "workspace_id": workspace_id,
                        "workflow_id": workflow_id,
                        "thread_id": thread_id,
                        "state": state,
                        "step": step,
                        "status": status,
                        "error_message": error_message,
                    }
                )
                .execute()
            )

        if result.data:
            return {"status": "success"}
        else:
            return {"error": "Failed to save state"}

    except Exception as e:
        print(f"⚠️  Save workflow state error: {e}")
        return {"error": str(e)}


def query_supabase(table: str, query: dict):
    """Generic query wrapper."""
    if not supabase_client:
        return []

    try:
        result = supabase_client.table(table).select("*").execute()
        return result.data or []
    except Exception as e:
        print(f"⚠️  Query error: {e}")
        return []
