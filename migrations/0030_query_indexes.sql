-- 0030_query_indexes.sql
-- Indexes for the queries the new build runs frequently. Audit Part 2 §8
-- (Slop #8) flagged missing indexes on columns used in WHERE clauses.
--
-- Idempotent: every CREATE uses IF NOT EXISTS.
--
-- NOTE: 0013 created agent_runs without client_slug/success; 0028 used
-- CREATE TABLE IF NOT EXISTS so those columns were never backfilled into the
-- existing table. We add them here before creating the indexes that need them.

-- ─── agent_runs — backfill columns skipped by 0028 ──────────────────
ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS client_slug TEXT NOT NULL DEFAULT 'default',
    ADD COLUMN IF NOT EXISTS success     BOOLEAN NOT NULL DEFAULT TRUE;

-- ─── agent_runs — indexes ────────────────────────────────────────────
-- OBSERVER queries: filter by (agent_name, success, started_at), order by started_at.
-- Worker dedup query: filter by (agent_name, success), order by started_at desc.
CREATE INDEX IF NOT EXISTS idx_agent_runs_client_started
    ON agent_runs (client_slug, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_success_started
    ON agent_runs (agent_name, success, started_at DESC);

-- ─── events ─────────────────────────────────────────────────────────
-- Event bus persistence reads by type + correlation; RSI reads by type window.
CREATE INDEX IF NOT EXISTS idx_events_type_emitted
    ON events (type, emitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_correlation
    ON events (correlation_id, emitted_at DESC);

-- ─── agent_approvals (Fix #1, migration 0029) ──────────────────────
-- Already has idx_agent_approvals_decision and idx_agent_approvals_client.
-- Add an index for the resume path (lookup by thread_id).
CREATE INDEX IF NOT EXISTS idx_agent_approvals_thread
    ON agent_approvals (thread_id);

-- ─── agent_pending_resumes (Fix #1) ────────────────────────────────
-- Resumer fetches by approval_id (already unique-indexed); add a partial
-- index on pending rows to speed expiry sweeps.
CREATE INDEX IF NOT EXISTS idx_pending_resumes_pending_expires
    ON agent_pending_resumes (expires_at)
    WHERE status = 'pending';

-- ─── rd_proposals (Fix #2 applier) ─────────────────────────────────
-- Persisted by core/improvement/applier.py. Queried by reviewer dashboard.
-- rd_proposals has no client_slug column (it is system-scoped, not per-client).
-- Index covers (status, created_at DESC) which is what the dashboard queries.
CREATE INDEX IF NOT EXISTS idx_rd_proposals_status_created
    ON rd_proposals (status, created_at DESC);

NOTIFY pgrst, 'reload schema';
