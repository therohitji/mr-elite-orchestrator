"""
Ingestion Router
================
Standalone endpoints for data ingestion tasks.
Completely separate from the main 5-phase pipeline.

Endpoints are called by the frontend after a user uploads
a memory, policy, or resource. Each endpoint processes ALL
pending items synchronously and returns the result.
"""

import logging
from fastapi import APIRouter, HTTPException

from app.ingestion.memory_embedder import process_pending_memories
from app.ingestion.policy_embedder import process_pending_policies
from app.ingestion.resource_embedder import process_pending_resources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/memory/embed")
async def embed_pending_memories():
    """
    Trigger memory embedding ingestion.

    Picks up ALL memory_items with embedding_status='pending',
    generates embeddings, writes to memory_vectors, and
    marks each row as 'ready' or 'failed'.
    """
    try:
        result = await process_pending_memories()
        return {"status": "success", **result}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[Ingestion] Memory embed error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/policy/embed")
async def embed_pending_policies():
    """
    Trigger policy embedding ingestion.

    Picks up ALL policy_rules with embedding_status='pending',
    generates embeddings, stores them inline on policy_rules,
    and marks each row as 'ready' or 'failed'.
    """
    try:
        result = await process_pending_policies()
        return {"status": "success", **result}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[Ingestion] Policy embed error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resource/embed")
async def embed_pending_resources():
    """
    Trigger resource document ingestion.

    Picks up ALL resources with status='UPLOADED' and resource_kind='knowledge',
    downloads files, extracts text, chunks, generates embeddings per chunk,
    inserts into resource_chunks, and marks each resource as 'READY' or 'FAILED'.
    """
    try:
        result = await process_pending_resources()
        return {"status": "success", **result}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[Ingestion] Resource embed error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/embed-all")
async def embed_all():
    """
    Trigger ALL embedding ingestion (memories + policies + resources).

    Convenience endpoint — processes everything synchronously and
    returns combined results.
    """
    try:
        memory_result = await process_pending_memories()
        policy_result = await process_pending_policies()
        resource_result = await process_pending_resources()
        return {
            "status": "success",
            "memory": memory_result,
            "policy": policy_result,
            "resource": resource_result,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"[Ingestion] Embed-all error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


