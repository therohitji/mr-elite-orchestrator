-- ============================================================================
-- MEMORY EMBEDDING MIGRATION
-- ============================================================================
-- Adds embedding_status + embedding_error to memory_items
-- Alters memory_vectors.embedding from VECTOR(1536) to VECTOR(768) for Gemini
-- ============================================================================

-- 1. Add embedding tracking columns to memory_items
ALTER TABLE public.memory_items
  ADD COLUMN IF NOT EXISTS embedding_status TEXT NOT NULL DEFAULT 'pending',
  ADD COLUMN IF NOT EXISTS embedding_error TEXT NULL;

-- 2. Index for fast pending-memory lookups
CREATE INDEX IF NOT EXISTS idx_memory_items_embedding_pending
  ON memory_items(embedding_status)
  WHERE embedding_status = 'pending';

-- 3. Alter memory_vectors embedding dimension from 1536 → 768 (Gemini text-embedding-004)
--    Drop the HNSW index first, alter column, then recreate index.
DROP INDEX IF EXISTS idx_memory_vectors_hnsw;

ALTER TABLE memory_vectors
  ALTER COLUMN embedding TYPE VECTOR(768);

CREATE INDEX idx_memory_vectors_hnsw
  ON memory_vectors USING hnsw (embedding vector_cosine_ops);

-- ============================================================================
-- END OF MIGRATION
-- ============================================================================
