-- 0035_system_mutex_and_superseded.sql
-- Two related primitives:
--   1. system_mutex — TTL-stealable distributed lock for cron/job dedup.
--      Acquisition uses ON CONFLICT WHERE expires_at < now() so a stale
--      lock from a crashed worker is auto-reclaimed by the next attempt.
--      Holder-scoped DELETE prevents one worker from releasing another's lock.
--   2. agent_runs.superseded_at — logical-kill signal for orphan threads.
--      When run_executor's 30-min timeout fires, the orphan ThreadPoolExecutor
--      thread cannot be terminated in Python. Setting superseded_at allows
--      run_lifecycle.transition(), checkpointer.resume_thread(), and any
--      future guard to refuse late writes from the dead run.

CREATE TABLE IF NOT EXISTS system_mutex (
    lock_name   TEXT PRIMARY KEY,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    acquired_by TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_system_mutex_expires_at
    ON system_mutex (expires_at);

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_agent_runs_superseded
    ON agent_runs (superseded_at)
    WHERE superseded_at IS NOT NULL;

-- ── Atomic mutex acquisition ────────────────────────────────────────────────
-- Single-roundtrip INSERT-or-steal-if-expired. Returns the actual holder so
-- Python can confirm ownership. Safe under concurrent acquirers — the ON
-- CONFLICT clause is evaluated under row lock.

CREATE OR REPLACE FUNCTION try_acquire_mutex(
    p_lock_name   TEXT,
    p_ttl_seconds INTEGER,
    p_holder_id   TEXT
) RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_holder TEXT;
BEGIN
    INSERT INTO system_mutex (lock_name, acquired_at, acquired_by, expires_at)
    VALUES (
        p_lock_name,
        now(),
        p_holder_id,
        now() + make_interval(secs => p_ttl_seconds)
    )
    ON CONFLICT (lock_name) DO UPDATE
        SET acquired_at = EXCLUDED.acquired_at,
            acquired_by = EXCLUDED.acquired_by,
            expires_at  = EXCLUDED.expires_at
        WHERE system_mutex.expires_at < now()
    RETURNING acquired_by INTO v_holder;

    -- If the ON CONFLICT WHERE clause failed (lock still live), nothing was
    -- updated and v_holder is NULL — fetch the current holder so the caller
    -- can log who is holding the lock.
    IF v_holder IS NULL THEN
        SELECT acquired_by INTO v_holder
        FROM system_mutex
        WHERE lock_name = p_lock_name;
    END IF;

    RETURN v_holder;
END;
$$;
