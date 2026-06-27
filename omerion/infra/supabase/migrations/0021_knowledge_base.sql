-- 0021_knowledge_base.sql
-- Knowledge Base ingestion pipeline tables.
-- Idempotent: all statements use IF NOT EXISTS / OR REPLACE.

-- pgvector must be enabled (was added in 0001 alongside uuid-ossp etc.)
CREATE EXTENSION IF NOT EXISTS vector;

-- ── document_chunks ──────────────────────────────────────────────────────────
-- Stores chunked + embedded content from Google Drive Knowledge Base documents.
-- embedding is vector(1536) matching OpenAI text-embedding-3-small.

CREATE TABLE IF NOT EXISTS document_chunks (
    id           uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id      text        NOT NULL,
    chunk_index  int         NOT NULL,
    content      text        NOT NULL,
    embedding    vector(1536),
    metadata     jsonb,
    created_at   timestamptz DEFAULT now(),
    UNIQUE (file_id, chunk_index)
);

-- HNSW index for fast approximate nearest-neighbour cosine search.
-- m=16, ef_construction=64 balances recall and build time for KB-scale data.
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS document_chunks_file_id_idx
    ON document_chunks (file_id);

-- ── document_index ───────────────────────────────────────────────────────────
-- Deduplication + audit log. One row per Drive file.
-- content_hash (sha256 of full text) is compared before re-embedding.

CREATE TABLE IF NOT EXISTS document_index (
    id             uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id        text        NOT NULL UNIQUE,
    file_name      text        NOT NULL,
    content_hash   text        NOT NULL,
    chunk_count    int         NOT NULL,
    mime_type      text,
    last_ingested  timestamptz DEFAULT now(),
    status         text        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    error_message  text
);

-- ── drive_watch_channels ──────────────────────────────────────────────────────
-- Tracks active Google Drive push-notification channels for auto-renewal.

CREATE TABLE IF NOT EXISTS drive_watch_channels (
    channel_id   text        PRIMARY KEY,
    resource_id  text        NOT NULL,
    expires_at   timestamptz NOT NULL,
    folder_id    text        NOT NULL,
    created_at   timestamptz DEFAULT now()
);

-- ── match_documents RPC ───────────────────────────────────────────────────────
-- Semantic similarity search over document_chunks.
-- Called by agents that need to query the Knowledge Base at inference time.

CREATE OR REPLACE FUNCTION match_documents(
    query_embedding  vector(1536),
    match_threshold  float   DEFAULT 0.7,
    match_count      int     DEFAULT 10,
    filter_file_id   text    DEFAULT NULL
)
RETURNS TABLE (
    id           uuid,
    file_id      text,
    chunk_index  int,
    content      text,
    metadata     jsonb,
    similarity   float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.file_id,
        dc.chunk_index,
        dc.content,
        dc.metadata,
        (1 - (dc.embedding <=> query_embedding))::float AS similarity
    FROM document_chunks dc
    WHERE
        (filter_file_id IS NULL OR dc.file_id = filter_file_id)
        AND (1 - (dc.embedding <=> query_embedding)) > match_threshold
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
