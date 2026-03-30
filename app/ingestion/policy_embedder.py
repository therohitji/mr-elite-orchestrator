"""
Policy Embedding Worker
=======================
Picks up policy_rules with embedding_status='pending',
generates embeddings via Gemini, updates the embedding column
inline on policy_rules, and marks status as 'ready' or 'failed'.

Usage: called from POST /ingestion/policy/embed
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


def fetch_pending_policies(limit: int = 50) -> List[Dict[str, Any]]:
    """
    SELECT * FROM policy_rules
    WHERE embedding_status = 'pending'
    LIMIT :limit
    """
    db = _get_db()
    result = (
        db.table("policy_rules")
        .select("*")
        .eq("embedding_status", "pending")
        .limit(limit)
        .execute()
    )
    return result.data or []


def lock_policy(policy_id: str) -> bool:
    """
    UPDATE policy_rules
    SET embedding_status = 'processing'
    WHERE id = :policy_id AND embedding_status = 'pending'

    Returns True if the row was actually updated (we got the lock).
    """
    db = _get_db()
    result = (
        db.table("policy_rules")
        .update({"embedding_status": "processing"})
        .eq("id", policy_id)
        .eq("embedding_status", "pending")
        .execute()
    )
    return bool(result.data)


def _build_embedding_text(row: Dict[str, Any]) -> str:
    """
    Build the text to embed from policy fields.
    Format: "{title}. {rule_text}. Domain: {domain}. Enforcement: {enforcement}. Type: {type}"
    Falls back gracefully if optional fields are missing.
    """
    parts = []

    title = row.get("title") or row.get("name")
    if title:
        parts.append(title)

    rule_text = row.get("rule_text") or row.get("policy_text") or ""
    if rule_text:
        parts.append(rule_text)

    domain = row.get("domain")
    if domain:
        parts.append(f"Domain: {domain}")

    enforcement = row.get("enforcement")
    if enforcement:
        parts.append(f"Enforcement: {enforcement}")

    policy_type = row.get("type") or row.get("policy_type")
    if policy_type:
        parts.append(f"Type: {policy_type}")

    return ". ".join(parts).strip() or rule_text


def store_policy_embedding(policy_id: str, embedding: list) -> None:
    """
    UPDATE policy_rules
    SET embedding = :vector,
        embedding_model = 'text-embedding-004',
        embedding_created_at = now(),
        embedding_status = 'ready'
    WHERE id = :policy_id
    """
    db = _get_db()
    db.table("policy_rules").update(
        {
            "embedding": embedding,
            "embedding_model": EMBEDDING_MODEL_NAME,
            "embedding_created_at": "now()",
            "embedding_status": "ready",
            "embedding_error": None,
        }
    ).eq("id", policy_id).execute()


def mark_failed(policy_id: str, error: str) -> None:
    """
    UPDATE policy_rules
    SET embedding_status = 'failed', embedding_error = :error
    WHERE id = :policy_id
    """
    db = _get_db()
    db.table("policy_rules").update(
        {"embedding_status": "failed", "embedding_error": error}
    ).eq("id", policy_id).execute()


# ─────────────────── ORCHESTRATOR ──────────────────


async def process_pending_policies(limit: int = 1000) -> Dict[str, Any]:
    """
    End-to-end policy embedding loop:
      1. Fetch pending policies
      2. Build embedding text (no chunking — one policy → one embedding)
      3. Generate embedding via Gemini
      4. Store embedding + mark 'ready', or mark 'failed'

    Returns summary dict with counts.
    """
    pending = fetch_pending_policies(limit=limit)
    logger.info(f"[PolicyEmbedder] Found {len(pending)} pending policies")

    processed = 0
    failed = 0
    skipped = 0

    for row in pending:
        policy_id = str(row["id"])
        try:
            # Step 2 — Build text to embed
            text = _build_embedding_text(row)

            # Step 4 — Generate embedding (atomic, no chunking)
            embedding = await call_gemini_embedding(text)

            # Step 5 — Store embedding inline on policy_rules
            store_policy_embedding(policy_id, embedding)
            processed += 1
            logger.info(f"[PolicyEmbedder] ✅ Embedded policy {policy_id}")

        except Exception as exc:
            failed += 1
            error_msg = str(exc)[:500]
            logger.error(f"[PolicyEmbedder] ❌ Failed {policy_id}: {error_msg}")
            try:
                mark_failed(policy_id, error_msg)
            except Exception as inner:
                logger.error(f"[PolicyEmbedder] Could not mark failed: {inner}")

    summary = {
        "total_pending": len(pending),
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
    }
    logger.info(f"[PolicyEmbedder] Done — {summary}")
    return summary
