-- 0034_state_change_log.sql
-- Immutable audit trail for agent_runs status transitions.
--
-- run_lifecycle.transition() already inserts into this table (wrapped in
-- try/except that logged a warning when the table didn't exist). The insert
-- site is correct; this migration just adds the schema so the audit trail
-- actually persists. Append-only by convention — no UPDATE/DELETE.

CREATE TABLE IF NOT EXISTS state_change_log (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    agent_name TEXT NOT NULL DEFAULT '',
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_state_change_log_run_id
    ON state_change_log (run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_state_change_log_agent_recent
    ON state_change_log (agent_name, created_at DESC);
