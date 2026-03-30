"""
PHASE 2: RESOURCES LAYER - PRODUCTION IMPLEMENTATION

Per resourcemain.txt: Steps 1-10
Implements resource retrieval (templates + knowledge base)
Integrated with Supabase Storage + vector DB for semantic search.
"""

import logging
from typing import Dict, List, Any, Optional

from app.phase1.atoms import ExecutionAtom

logger = logging.getLogger(__name__)


class ResourceEngine:
    """
    Resources Layer: Retrieval + Fetching workflow

    Step 6: Semantic Retrieval RPCs (match_resources + match_resource_chunks)
    Step 7: Resource Fetcher SDK (Mr. Elite-facing)
    """

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client

    async def find_resources_for_atom(
        self, atom: ExecutionAtom, workspace_id: str, is_template: bool = None
    ) -> Dict[str, Any]:
        """
        Main resource search: find files + chunks relevant to atom

        Args:
            atom: ExecutionAtom from Phase 1
            workspace_id: Tenant context
            is_template: Filter by template type (optional)

        Returns:
            {
              "status": "found" | "not_found",
              "resources": [{id, title, type, similarity, storage_path}, ...],
              "chunks": [{resource_id, chunk_content, similarity}, ...],
              "selected_resource": {...} or None
            }
        """
        logger.info(f"[Resources] Searching for atom {atom.id}: {atom.description}")

        try:
            # ─────────────────── STEP 6A: FIND WHOLE DOCUMENTS ──────────────────
            matched_resources = await self._match_resources(
                atom, workspace_id, is_template
            )

            # ─────────────────── STEP 6B: FIND RELEVANT CHUNKS ──────────────────
            matched_chunks = await self._match_resource_chunks(atom, workspace_id)

            # ─────────────────── SMART SELECTION ──────────────────
            # If we have good chunk matches, return them
            # Otherwise, return the whole resource file
            if matched_chunks:
                logger.info(f"[Resources] Found {len(matched_chunks)} relevant chunks")
                return {
                    "status": "found",
                    "resource_type": "chunks",
                    "resources": matched_resources,
                    "chunks": matched_chunks,
                    "selected_resource": (
                        matched_resources[0] if matched_resources else None
                    ),
                }
            elif matched_resources:
                logger.info(
                    f"[Resources] Found {len(matched_resources)} resource files"
                )
                return {
                    "status": "found",
                    "resource_type": "file",
                    "resources": matched_resources,
                    "chunks": [],
                    "selected_resource": matched_resources[0],
                }
            else:
                logger.info(f"[Resources] No resources found for atom {atom.id}")
                return {
                    "status": "not_found",
                    "resource_type": None,
                    "resources": [],
                    "chunks": [],
                    "selected_resource": None,
                }

        except Exception as e:
            logger.error(f"[Resources] Search failed: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "resources": [],
                "chunks": [],
                "selected_resource": None,
            }

    async def _match_resources(
        self, atom: ExecutionAtom, workspace_id: str, is_template: bool = None
    ) -> List[Dict[str, Any]]:
        """
        Step 6A: RPC match_resources

        Find whole document files by similarity
        Filter by:
          - workspace_id (tenant)
          - is_active = true
          - is_template (optional)
          - Cosine similarity > 0.7
        """
        try:
            from app.core.supabase_client import query_resources_vector
            from app.core.gemini_client import call_gemini_embedding

            # Generate embedding for atom description
            embedding = await call_gemini_embedding(atom.description)

            if not embedding:
                logger.warning("[Resources] Failed to generate embedding")
                return []

            # Query Supabase RPC: match_resources
            resources = await query_resources_vector(
                workspace_id=workspace_id,
                embedding=embedding,
                is_template=is_template,
                threshold=0.7,
                limit=3,
            )

            logger.info(f"[Resources] File match returned {len(resources)} resources")
            return resources

        except Exception as e:
            logger.error(f"[Resources] File matching failed: {str(e)}")
            return []

    async def _match_resource_chunks(
        self, atom: ExecutionAtom, workspace_id: str
    ) -> List[Dict[str, Any]]:
        """
        Step 6B: RPC match_resource_chunks

        Find relevant paragraphs/sections within documents
        Return chunks with source document name + similarity
        """
        try:
            from app.core.supabase_client import query_resource_chunks_vector
            from app.core.gemini_client import call_gemini_embedding

            # Generate embedding for atom description
            embedding = await call_gemini_embedding(atom.description)

            if not embedding:
                return []

            # Query Supabase RPC: match_resource_chunks
            chunks = await query_resource_chunks_vector(
                workspace_id=workspace_id,
                embedding=embedding,
                threshold=0.7,
                limit=5,
            )

            logger.info(f"[Resources] Chunk match returned {len(chunks)} chunks")

            # Log resource access for audit
            if chunks:
                try:
                    from app.core.supabase_client import log_resource_access

                    for chunk in chunks:
                        await log_resource_access(
                            workspace_id=workspace_id,
                            atom_id=atom.id,
                            resource_id=chunk.get("resource_id", "unknown"),
                            resource_type="chunk",
                            retrieved=True,
                            source=chunk.get("source_document", ""),
                        )
                except Exception as log_err:
                    logger.warning(f"[Resources] Audit log error: {log_err}")

            return chunks

        except Exception as e:
            logger.error(f"[Resources] Chunk matching failed: {str(e)}")
            return []

    async def get_template_for_atom(
        self, atom: ExecutionAtom, workspace_id: str, template_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        Helper: Find a specific template type for atom

        Example: template_type = "offer_letter" for draft_offer tool
        """
        result = await self.find_resources_for_atom(
            atom, workspace_id, is_template=True
        )

        if result["status"] == "found":
            return result["selected_resource"]

        logger.warning(
            f"[Resources] No template found for {template_type} in {workspace_id}"
        )
        return None


# Synchronous wrapper for legacy code
def run_resource_check(atom: ExecutionAtom) -> Dict[str, Any]:
    """
    Synchronous legacy wrapper for resource checking.
    """
    import asyncio

    engine = ResourceEngine()
    loop = asyncio.get_event_loop()
    try:
        return loop.run_until_complete(
            engine.find_resources_for_atom(atom, workspace_id="default")
        )
    except RuntimeError:
        return asyncio.run(engine.find_resources_for_atom(atom, workspace_id="default"))
