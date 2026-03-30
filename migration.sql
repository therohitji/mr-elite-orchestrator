-- Run this in Supabase SQL Editor: https://app.supabase.com/project/_/sql

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Create policy_rules table
CREATE TABLE IF NOT EXISTS policy_rules (
    id BIGSERIAL PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('blocking', 'guiding', 'approval')),
    rule_text TEXT NOT NULL,
    embedding vector(768),
    similarity_threshold FLOAT DEFAULT 0.85,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 3. Create indexes
CREATE INDEX IF NOT EXISTS idx_policy_rules_type ON policy_rules(type);
CREATE INDEX IF NOT EXISTS idx_policy_rules_embedding ON policy_rules USING ivfflat (embedding vector_cosine_ops);

-- 4. Create vector similarity search function
CREATE OR REPLACE FUNCTION match_policies (
    query_embedding vector(768),
    policy_type TEXT DEFAULT 'blocking',
    similarity_threshold FLOAT DEFAULT 0.85
)
RETURNS TABLE (
    id BIGINT,
    type TEXT,
    rule_text TEXT,
    similarity FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        p.id,
        p.type,
        p.rule_text,
        (1 - (p.embedding <=> query_embedding))::float as similarity
    FROM policy_rules p
    WHERE p.type = policy_type
    AND (1 - (p.embedding <=> query_embedding)) > similarity_threshold
    ORDER BY similarity DESC
    LIMIT 5;
END;
$$ LANGUAGE plpgsql;
