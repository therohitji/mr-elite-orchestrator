"""
Knowledge Retriever for RAG-Augmented Query Responses
=====================================================
Retrieves relevant memories, policies, and resource chunks
via vector similarity search for query intent responses.

Only used when user asks a QUERY — not for chat, task, or goal intents
(those are handled by Phase 2 enrichment).

Usage: called from run_phase1_pipeline() when classification.intent == "query"
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from app.core.gemini_client import call_gemini_embedding

logger = logging.getLogger(__name__)


# ─────────────────── RESULT DATACLASS ──────────────────


@dataclass
class KnowledgeContext:
    """Retrieved knowledge from all 3 sources."""
    memories: List[Dict[str, Any]] = field(default_factory=list)
    policies: List[Dict[str, Any]] = field(default_factory=list)
    resource_chunks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def has_context(self) -> bool:
        return bool(self.memories or self.policies or self.resource_chunks)


# ─────────────────── RETRIEVER ──────────────────


class KnowledgeRetriever:
    """
    Retrieves relevant knowledge from memories, policies, and resources
    for a given user query via vector similarity search.
    """

    # Thresholds — intentionally lower than Phase 2 enforcement
    # since we want broad contextual recall for response generation
    MEMORY_THRESHOLD = 0.5
    POLICY_THRESHOLD = 0.4
    RESOURCE_THRESHOLD = 0.3

    # Limits
    MEMORY_LIMIT = 3
    POLICY_LIMIT = 5
    RESOURCE_LIMIT = 5

    async def retrieve_for_query(
        self,
        query: str,
        org_id: str,
        workspace_id: str,
    ) -> KnowledgeContext:
        """
        Retrieve relevant knowledge for a user query.

        1. Embed the query
        2. Run 3 RPCs in parallel (memories, policies, resources)
        3. Return structured KnowledgeContext

        Args:
            query: The user's query text
            org_id: Organization ID (for memory scoping)
            workspace_id: Workspace ID (for policy/resource scoping)
        """
        try:
            # Step 1: Embed the query
            query_embedding = await call_gemini_embedding(query)
            if not query_embedding or all(v == 0.0 for v in query_embedding):
                logger.warning("[KnowledgeRetriever] Failed to embed query, skipping retrieval")
                return KnowledgeContext()

            # Step 2: Run all 3 RPCs in parallel
            memories, policies, chunks = await asyncio.gather(
                self._search_memories(query_embedding, org_id),
                self._search_policies(query_embedding, workspace_id),
                self._search_resource_chunks(query_embedding, workspace_id),
                return_exceptions=True,
            )

            # Handle individual failures gracefully
            if isinstance(memories, Exception):
                logger.warning(f"[KnowledgeRetriever] Memory search failed: {memories}")
                memories = []
            if isinstance(policies, Exception):
                logger.warning(f"[KnowledgeRetriever] Policy search failed: {policies}")
                policies = []
            if isinstance(chunks, Exception):
                logger.warning(f"[KnowledgeRetriever] Resource search failed: {chunks}")
                chunks = []

            result = KnowledgeContext(
                memories=memories,
                policies=policies,
                resource_chunks=chunks,
            )

            logger.info(
                f"[KnowledgeRetriever] Retrieved: "
                f"{len(result.memories)} memories, "
                f"{len(result.policies)} policies, "
                f"{len(result.resource_chunks)} resource chunks"
            )
            return result

        except Exception as e:
            logger.error(f"[KnowledgeRetriever] Retrieval failed: {e}", exc_info=True)
            return KnowledgeContext()

    async def _search_memories(self, embedding: list, org_id: str) -> list:
        """Search memory_vectors via match_memories RPC."""
        from app.core.supabase_client import match_memories_rpc

        return await match_memories_rpc(
            org_id=org_id,
            query_embedding=embedding,
            limit=self.MEMORY_LIMIT,
            threshold=self.MEMORY_THRESHOLD,
        )

    async def _search_policies(self, embedding: list, workspace_id: str) -> list:
        """Search policy_rules via match_policies RPC."""
        from app.core.supabase_client import query_policies_vector

        return await query_policies_vector(
            workspace_id=workspace_id,
            embedding=embedding,
            threshold=self.POLICY_THRESHOLD,
            limit=self.POLICY_LIMIT,
        )

    async def _search_resource_chunks(self, embedding: list, workspace_id: str) -> list:
        """Search resource_chunks via match_resource_chunks RPC."""
        from app.core.supabase_client import query_resource_chunks_vector

        return await query_resource_chunks_vector(
            workspace_id=workspace_id,
            embedding=embedding,
            threshold=self.RESOURCE_THRESHOLD,
            limit=self.RESOURCE_LIMIT,
        )

    @staticmethod
    def format_as_prompt_context(knowledge: KnowledgeContext) -> str:
        """
        Format retrieved knowledge into a structured text block
        for injection into the LLM prompt.

        Returns empty string if no context found.
        """
        if not knowledge.has_context:
            return ""

        sections = []
        sections.append("=" * 50)
        sections.append("KNOWLEDGE CONTEXT (from your organization's data)")
        sections.append("=" * 50)

        # Policies
        if knowledge.policies:
            sections.append("\n📋 COMPANY POLICIES:")
            for i, policy in enumerate(knowledge.policies, 1):
                rule_text = policy.get("rule_text") or policy.get("policy_text") or ""
                policy_type = policy.get("policy_type") or policy.get("type") or "general"
                title = policy.get("title") or policy.get("name") or f"Policy {i}"
                similarity = float(policy.get("similarity", 0))
                sections.append(
                    f"  {i}. [{policy_type.upper()}] {title}\n"
                    f"     {rule_text}\n"
                    f"     (relevance: {similarity:.0%})"
                )

        # Resource chunks
        if knowledge.resource_chunks:
            sections.append("\n📚 KNOWLEDGE BASE:")
            for i, chunk in enumerate(knowledge.resource_chunks, 1):
                content = chunk.get("chunk_content") or chunk.get("content") or ""
                source = chunk.get("source_document") or chunk.get("resource_title") or "Document"
                similarity = float(chunk.get("similarity", 0))
                # Truncate long chunks to avoid overwhelming the prompt
                if len(content) > 800:
                    content = content[:800] + "..."
                sections.append(
                    f"  {i}. Source: {source} (relevance: {similarity:.0%})\n"
                    f"     {content}"
                )

        # Memories
        if knowledge.memories:
            sections.append("\n🧠 RELEVANT MEMORIES:")
            for i, memory in enumerate(knowledge.memories, 1):
                title = memory.get("title") or f"Memory {i}"
                content = memory.get("content") or ""
                mem_type = memory.get("memory_type") or "general"
                similarity = float(memory.get("similarity", 0))
                if len(content) > 400:
                    content = content[:400] + "..."
                sections.append(
                    f"  {i}. [{mem_type}] {title} (relevance: {similarity:.0%})\n"
                    f"     {content}"
                )

        sections.append("=" * 50)
        return "\n".join(sections)
