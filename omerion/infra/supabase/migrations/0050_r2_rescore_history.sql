-- Migration 0050: Create rd_oss_candidates (R2 schema) + rescore_history column
--
-- The original oss_candidates table (migration 0005) used a legacy architecture
-- schema. R2 OSS Scout was written against a newer schema. This migration creates
-- the canonical rd_oss_candidates table R2 expects, then adds the rescore_history
-- column and RPC used for maturity trend tracking.
--
-- Idempotent: safe to re-run. CREATE TABLE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS.

-- ── Create the table if it doesn't already exist ─────────────────────────────
CREATE TABLE IF NOT EXISTS rd_oss_candidates (
    candidate_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_url         TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    description      TEXT,
    stars            INT NOT NULL DEFAULT 0,
    language         TEXT,
    license          TEXT,
    search_tag       TEXT,
    integration_type TEXT NOT NULL DEFAULT 'reference_only',
    impact_tag       TEXT NOT NULL DEFAULT 'asap',
    recommendation   TEXT,
    overall_score    NUMERIC(5,4) NOT NULL DEFAULT 0,
    rubric_fit       NUMERIC(5,4) NOT NULL DEFAULT 0,
    rubric_maturity  NUMERIC(5,4) NOT NULL DEFAULT 0,
    rubric_composability NUMERIC(5,4) NOT NULL DEFAULT 0,
    rubric_risk      NUMERIC(5,4) NOT NULL DEFAULT 0,
    rubric           JSONB NOT NULL DEFAULT '{}'::jsonb,
    scored_by        TEXT NOT NULL DEFAULT 'haiku',
    rescore_history  JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Add rescore_history to existing rows if table already existed without it ──
ALTER TABLE rd_oss_candidates
    ADD COLUMN IF NOT EXISTS rescore_history JSONB NOT NULL DEFAULT '[]'::jsonb;

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_rd_oss_candidates_score
    ON rd_oss_candidates (overall_score DESC);

CREATE INDEX IF NOT EXISTS idx_rd_oss_candidates_impact
    ON rd_oss_candidates (impact_tag, overall_score DESC);

-- ── RPC: atomically append a rescore entry ───────────────────────────────────
CREATE OR REPLACE FUNCTION r2_append_rescore_history(
    p_repo_url TEXT,
    p_entry    JSONB
) RETURNS VOID LANGUAGE SQL AS $$
    UPDATE rd_oss_candidates
       SET rescore_history = rescore_history || jsonb_build_array(p_entry),
           updated_at      = now()
     WHERE repo_url = p_repo_url;
$$;

COMMENT ON FUNCTION r2_append_rescore_history IS
    'Append one rescore entry to history without overwriting. Called by R2 on existing repos.';

COMMENT ON TABLE rd_oss_candidates IS
    'R2 OSS Scout candidates. rescore_history tracks score drift across weekly runs for R3 maturity trend.';
