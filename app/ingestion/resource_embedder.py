"""
Resource Embedding / Ingestion Worker
======================================
Picks up resources with status='UPLOADED', resource_kind='knowledge',
downloads from Supabase Storage, extracts text, chunks, generates
embeddings, inserts into resource_chunks, marks resource as READY.

Usage: called from POST /ingestion/resource/embed
"""

import logging
import io
import math
from typing import List, Dict, Any, Optional, Tuple

from app.core.supabase_client import supabase_service_client
from app.core.gemini_client import call_gemini_embedding, call_gemini_embedding_batch

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "gemini-embedding-001"  # Gemini 768-dim

# ─────────────────── CHUNKING CONFIG ──────────────────
TARGET_CHUNK_TOKENS = 500   # centre of 400-600 range
MIN_CHUNK_TOKENS = 400
MAX_CHUNK_TOKENS = 600
OVERLAP_TOKENS = 80
APPROX_CHARS_PER_TOKEN = 4  # conservative estimate for English text


# ─────────────────── DB HELPERS ──────────────────


def _get_db():
    """Return the service-role Supabase client (bypasses RLS)."""
    if not supabase_service_client:
        raise RuntimeError("supabase_service_client is not initialised")
    return supabase_service_client


def fetch_pending_resources(limit: int = 10) -> List[Dict[str, Any]]:
    """
    SELECT * FROM resources
    WHERE status = 'UPLOADED'
      AND resource_kind = 'knowledge'
      AND disabled = false
    LIMIT :limit
    """
    db = _get_db()
    result = (
        db.table("resources")
        .select("*")
        .eq("status", "UPLOADED")
        .eq("resource_kind", "knowledge")
        .eq("disabled", False)
        .limit(limit)
        .execute()
    )
    return result.data or []


def lock_resource(resource_id: str) -> bool:
    """
    UPDATE resources SET status = 'PROCESSING'
    WHERE id = :resource_id AND status = 'UPLOADED'

    Returns True if locked successfully.
    """
    db = _get_db()
    result = (
        db.table("resources")
        .update({"status": "PROCESSING"})
        .eq("id", resource_id)
        .eq("status", "UPLOADED")
        .execute()
    )
    return bool(result.data)


def mark_ready(resource_id: str) -> None:
    """UPDATE resources SET status = 'READY' WHERE id = :resource_id"""
    db = _get_db()
    db.table("resources").update(
        {"status": "READY", "failure_reason": None}
    ).eq("id", resource_id).execute()


def mark_failed(resource_id: str, error: str) -> None:
    """
    UPDATE resources
    SET status = 'FAILED', failure_reason = :error
    WHERE id = :resource_id
    """
    db = _get_db()
    db.table("resources").update(
        {"status": "FAILED", "failure_reason": error[:1000]}
    ).eq("id", resource_id).execute()


def insert_chunks(chunks: List[Dict[str, Any]]) -> None:
    """Batch-insert chunk rows into resource_chunks."""
    if not chunks:
        return
    db = _get_db()
    db.table("resource_chunks").insert(chunks).execute()


# ─────────────────── FILE DOWNLOAD ──────────────────


def download_file(bucket: str, storage_path: str) -> bytes:
    """
    Download file bytes from Supabase Storage using the service-role client.
    """
    db = _get_db()
    data = db.storage.from_(bucket).download(storage_path)
    return data


def _detect_file_type(storage_path: str) -> str:
    """Detect file type from extension."""
    path_lower = storage_path.lower()
    if path_lower.endswith(".pdf"):
        return "pdf"
    elif path_lower.endswith(".docx"):
        return "docx"
    elif path_lower.endswith(".doc"):
        return "doc"
    elif path_lower.endswith(".txt"):
        return "txt"
    elif path_lower.endswith(".md"):
        return "md"
    else:
        return "txt"  # fallback to raw text


# ─────────────────── TEXT EXTRACTION ──────────────────


def extract_text_pdf(file_bytes: bytes) -> List[Tuple[int, str]]:
    """
    Extract text page-by-page from a PDF.
    Returns list of (page_number, text).
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages.append((i + 1, text))
    return pages


def extract_text_docx(file_bytes: bytes) -> List[Tuple[int, str]]:
    """
    Extract paragraphs from a DOCX file.
    Returns list of (paragraph_index, text).
    """
    from docx import Document

    doc = Document(io.BytesIO(file_bytes))
    paragraphs = []
    for i, para in enumerate(doc.paragraphs):
        text = para.text.strip()
        if text:
            paragraphs.append((i + 1, text))
    return paragraphs


def extract_text_plain(file_bytes: bytes) -> List[Tuple[int, str]]:
    """
    Raw text extraction for .txt / .md files.
    Returns [(1, full_text)].
    """
    text = file_bytes.decode("utf-8", errors="replace").strip()
    if text:
        return [(1, text)]
    return []


def extract_text(file_bytes: bytes, file_type: str) -> List[Tuple[int, str]]:
    """
    Route to the correct extractor based on file type.
    Returns list of (page_or_section_number, text).
    """
    if file_type == "pdf":
        return extract_text_pdf(file_bytes)
    elif file_type == "docx":
        return extract_text_docx(file_bytes)
    else:
        return extract_text_plain(file_bytes)


# ─────────────────── CHUNKING ──────────────────


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: chars / 4."""
    return max(1, len(text) // APPROX_CHARS_PER_TOKEN)


def chunk_text(
    pages: List[Tuple[int, str]],
) -> List[Dict[str, Any]]:
    """
    Chunk extracted pages/paragraphs into 400-600 token windows
    with ~80 token overlap. Returns list of chunk dicts with:
      chunk_index, chunk_content, chunk_tokens, page_start, page_end
    """
    # Flatten all pages into one stream, tracking page boundaries
    full_text = ""
    page_char_ranges = []  # (start_char, end_char, page_num)

    for page_num, page_text in pages:
        start = len(full_text)
        if full_text:
            full_text += "\n\n"
            start = len(full_text)
        full_text += page_text
        end = len(full_text)
        page_char_ranges.append((start, end, page_num))

    if not full_text.strip():
        return []

    target_chars = TARGET_CHUNK_TOKENS * APPROX_CHARS_PER_TOKEN
    overlap_chars = OVERLAP_TOKENS * APPROX_CHARS_PER_TOKEN
    min_chars = MIN_CHUNK_TOKENS * APPROX_CHARS_PER_TOKEN

    chunks = []
    chunk_index = 0
    pos = 0
    text_len = len(full_text)

    while pos < text_len:
        end = min(pos + target_chars, text_len)

        # Try to break at a sentence/paragraph boundary
        if end < text_len:
            # Look for natural break points near the target
            search_start = max(pos + min_chars, end - 200)
            best_break = end

            for delim in ["\n\n", "\n", ". ", "! ", "? ", "; "]:
                idx = full_text.rfind(delim, search_start, end + 100)
                if idx > search_start:
                    best_break = idx + len(delim)
                    break

            end = best_break

        chunk_content = full_text[pos:end].strip()
        if not chunk_content:
            pos = end
            continue

        chunk_tokens = _estimate_tokens(chunk_content)

        # Determine page_start and page_end for this chunk
        page_start = None
        page_end = None
        for (r_start, r_end, page_num) in page_char_ranges:
            if r_end > pos and r_start < end:
                if page_start is None:
                    page_start = page_num
                page_end = page_num

        chunks.append({
            "chunk_index": chunk_index,
            "chunk_content": chunk_content,
            "chunk_tokens": chunk_tokens,
            "page_start": page_start,
            "page_end": page_end,
        })

        chunk_index += 1

        # Advance with overlap
        pos = max(pos + 1, end - overlap_chars)

    return chunks


# ─────────────────── ORCHESTRATOR ──────────────────


async def process_pending_resources(limit: int = 1000) -> Dict[str, Any]:
    """
    End-to-end resource ingestion loop:
      1. Fetch pending resources (status=UPLOADED, kind=knowledge)
      2. Download file from Supabase Storage
      3. Extract text (PDF/DOCX/TXT)
      4. Chunk text (400-600 tokens, ~80 overlap)
      5. Generate embedding per chunk
      6. Insert into resource_chunks
      7. Mark resource READY (or FAILED)

    Returns summary dict with counts.
    """
    pending = fetch_pending_resources(limit=limit)
    logger.info(f"[ResourceEmbedder] Found {len(pending)} pending resources")

    processed = 0
    failed = 0
    skipped = 0

    for row in pending:
        resource_id = str(row["id"])
        storage_path = row.get("storage_path")
        workspace_id = row.get("workspace_id")

        try:
            if not storage_path:
                raise ValueError("No storage_path on resource row")

            # Step 3 — Download file from Supabase Storage
            bucket = row.get("bucket") or "resources"
            logger.info(f"[ResourceEmbedder] Downloading {bucket}/{storage_path}")
            file_bytes = download_file(bucket, storage_path)

            # Step 4 — Extract text
            file_type = _detect_file_type(storage_path)
            pages = extract_text(file_bytes, file_type)

            if not pages:
                raise ValueError(f"No text extracted from {storage_path} (type={file_type})")

            total_chars = sum(len(t) for _, t in pages)
            logger.info(
                f"[ResourceEmbedder] Extracted {len(pages)} pages/sections, "
                f"{total_chars} chars from {storage_path}"
            )

            # Step 5 — Chunk
            chunks = chunk_text(pages)

            if not chunks:
                raise ValueError("Chunking produced zero chunks")

            logger.info(f"[ResourceEmbedder] Created {len(chunks)} chunks")

            # Step 5+6 — Batch embed all chunks in one API call
            chunk_texts = [chunk["chunk_content"] for chunk in chunks]
            embeddings = await call_gemini_embedding_batch(chunk_texts)

            logger.info(
                f"[ResourceEmbedder] Embedded {len(chunks)} chunks in "
                f"{(len(chunks) - 1) // 100 + 1} API call(s)"
            )

            # Step 7 — Build rows and insert
            chunk_rows = []
            for chunk, embedding in zip(chunks, embeddings):
                chunk_rows.append({
                    "workspace_id": workspace_id,
                    "resource_id": resource_id,
                    "chunk_index": chunk["chunk_index"],
                    "chunk_content": chunk["chunk_content"],
                    "chunk_tokens": chunk["chunk_tokens"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "embedding": embedding,
                    "embedding_model": EMBEDDING_MODEL_NAME,
                })

            insert_chunks(chunk_rows)

            # Step 8 — Mark READY
            mark_ready(resource_id)
            processed += 1
            logger.info(
                f"[ResourceEmbedder] ✅ Resource {resource_id}: "
                f"{len(chunk_rows)} chunks embedded"
            )

        except Exception as exc:
            failed += 1
            error_msg = str(exc)[:500]
            logger.error(f"[ResourceEmbedder] ❌ Failed {resource_id}: {error_msg}")
            try:
                mark_failed(resource_id, error_msg)
            except Exception as inner:
                logger.error(f"[ResourceEmbedder] Could not mark failed: {inner}")

    summary = {
        "total_pending": len(pending),
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
    }
    logger.info(f"[ResourceEmbedder] Done — {summary}")
    return summary
