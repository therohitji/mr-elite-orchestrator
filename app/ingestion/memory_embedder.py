"""
Memory Embedding Worker
=======================
Picks up memory_items with embedding_status='pending',
generates embeddings via Gemini, writes to memory_vectors,
and marks status as 'ready' or 'failed'.

Usage: called from POST /ingestion/memory/embed
"""

import logging
from typing import List, Dict, Any

from app.core.supabase_client import supabase_service_client
from app.core.gemini_client import call_gemini_embedding

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "gemini-embedding-001"  # Gemini 768-dim


# ─────────────────── DB HELPERS ──────────────────


def _get_db():
    """Return the service-role Supabase client (bypasses RLS)."""
    if not supabase_service_client:
        raise RuntimeError("supabase_service_client is not initialised")
    return supabase_service_client


def fetch_pending_memories(limit: int = 50) -> List[Dict[str, Any]]:
    """
    SELECT * FROM memory_items
    WHERE embedding_status = 'pending' AND status = 'active'
    LIMIT :limit
    """
    db = _get_db()
    result = (
        db.table("memory_items")
        .select("*")
        .eq("embedding_status", "pending")
        .eq("status", "active")
        .limit(limit)
        .execute()
    )
    return result.data or []


def lock_memory(memory_id: str) -> bool:
    """
    UPDATE memory_items
    SET embedding_status = 'processing'
    WHERE id = :memory_id AND embedding_status = 'pending'

    Returns True if the row was actually updated (we got the lock).
    """
    db = _get_db()
    result = (
        db.table("memory_items")
        .update({"embedding_status": "processing"})
        .eq("id", memory_id)
        .eq("embedding_status", "pending")
        .execute()
    )
    return bool(result.data)


def upsert_memory_vector(
    memory_id: str,
    org_id: str,
    workspace_id: str,
    embedding: list,
) -> None:
    """
    INSERT INTO memory_vectors (memory_id, org_id, workspace_id, embedding, embedding_model)
    VALUES (...)
    ON CONFLICT (memory_id) DO UPDATE SET
      embedding = excluded.embedding,
      embedding_model = excluded.embedding_model,
      embedded_at = now();
    """
    db = _get_db()
    db.table("memory_vectors").upsert(
        {
            "memory_id": memory_id,
            "org_id": org_id,
            "embedding": embedding,
            "embedding_model": EMBEDDING_MODEL_NAME,
        },
        on_conflict="memory_id",
    ).execute()


def mark_ready(memory_id: str) -> None:
    """UPDATE memory_items SET embedding_status = 'ready' WHERE id = :memory_id"""
    db = _get_db()
    db.table("memory_items").update(
        {"embedding_status": "ready", "embedding_error": None}
    ).eq("id", memory_id).execute()


def mark_failed(memory_id: str, error: str) -> None:
    """
    UPDATE memory_items
    SET embedding_status = 'failed', embedding_error = :error
    WHERE id = :memory_id
    """
    db = _get_db()
    db.table("memory_items").update(
        {"embedding_status": "failed", "embedding_error": error}
    ).eq("id", memory_id).execute()


# ─────────────────── ORCHESTRATOR ──────────────────


async def process_pending_memories(limit: int = 1000) -> Dict[str, Any]:
    """
    End-to-end ingestion loop:
      1. Fetch pending memories
      2. Generate embedding (no chunking — memory is atomic)
      3. Upsert into memory_vectors
      4. Mark 'ready' or 'failed'

    Returns summary dict with counts.
    """
    pending = fetch_pending_memories(limit=limit)
    logger.info(f"[MemoryEmbedder] Found {len(pending)} pending memories")

    processed = 0
    failed = 0
    skipped = 0

    for row in pending:
        memory_id = row["id"]
        try:
            # Step 2 — Embed (memory is atomic, no chunking)
            content = row["content"]
            embedding = await call_gemini_embedding(content)

            # Step 4 — Upsert vector
            upsert_memory_vector(
                memory_id=memory_id,
                org_id=row["org_id"],
                workspace_id=row.get("workspace_id"),
                embedding=embedding,
            )

            # Step 5 — Mark ready
            mark_ready(memory_id)
            processed += 1
            logger.info(f"[MemoryEmbedder] ✅ Embedded memory {memory_id}")

        except Exception as exc:
            failed += 1
            error_msg = str(exc)[:500]
            logger.error(f"[MemoryEmbedder] ❌ Failed {memory_id}: {error_msg}")
            try:
                mark_failed(memory_id, error_msg)
            except Exception as inner:
                logger.error(f"[MemoryEmbedder] Could not mark failed: {inner}")

    summary = {
        "total_pending": len(pending),
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
    }
    logger.info(f"[MemoryEmbedder] Done — {summary}")
    return summary
