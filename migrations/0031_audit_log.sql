-- 0031_audit_log.sql
-- Immutable append-only status transition history + application-level mutex for RSI synthesis.

-- state_change_log: one row per status transition on any agent run.
-- Rows are never updated or deleted — only inserted.
CREATE TABLE IF NOT EXISTS state_change_log (
    id          BIGSERIAL PRIMARY KEY,
    run_id      UUID,
    agent_name  TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta        JSONB
);

CREATE INDEX IF NOT EXISTS idx_scl_run_id    ON state_change_log (run_id);
CREATE INDEX IF NOT EXISTS idx_scl_changed_at ON state_change_log (changed_at);

-- system_mutex: lightweight application-level advisory lock.
-- INSERT ON CONFLICT DO NOTHING is atomic; DELETE releases the lock.
-- Used by worker_main._weekly_rsi() to prevent duplicate synthesis runs
-- across parallel worker processes (pg_try_advisory_lock is session-scoped
-- and does not survive PostgREST connection pooling).
CREATE TABLE IF NOT EXISTS system_mutex (
    name        TEXT PRIMARY KEY,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    worker_id   TEXT
);
