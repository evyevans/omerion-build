-- 0063_production_hardening.sql
-- Wave 4 production hardening: three schema additions.

-- 1. deployment_status: add 'pending' value.
--    Used by build_orchestrator/contracts.py (deployment_status default="pending").
--    Without this, any deployment write raises:
--      invalid input value for enum deployment_status: "pending"
--    ALTER TYPE ADD VALUE cannot run inside a transaction on PG < 12.
--    Supabase runs PG 15+ so this is safe in a transaction block.
ALTER TYPE deployment_status ADD VALUE IF NOT EXISTS 'pending' AFTER 'queued';

-- 2. agent_runs: add hitl_expires_at for sweeper HITL-aware gate.
--    NULL  = normal running run (sweeper may close after 30-min timeout).
--    value = the run is heading for interrupt(); sweeper must not kill it
--            until this timestamp passes.
--    Agents set this column just before calling interrupt() so the sweeper
--    has a reliable signal that does not require checkpointer inspection.
ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS hitl_expires_at TIMESTAMPTZ;

-- Partial index: only non-null rows need to be scannable by the sweeper.
CREATE INDEX IF NOT EXISTS idx_agent_runs_hitl_expires_at
    ON agent_runs (hitl_expires_at)
    WHERE hitl_expires_at IS NOT NULL;

-- 3. effect_log: idempotency table for external side-effects.
--    Consumers: trainer GitHub commits (Wave 4), future email sends, webhook posts.
--    Pattern: check idempotency_key before executing; insert immediately after success.
--    TTL: clean up rows older than 30 days via nightly checkpointer job.
CREATE TABLE IF NOT EXISTS effect_log (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key  TEXT         UNIQUE NOT NULL,
    effect_type      TEXT         NOT NULL,
    result           JSONB,
    created_at       TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_effect_log_key        ON effect_log (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_effect_log_created_at ON effect_log (created_at);

COMMENT ON TABLE effect_log IS
    'Wave 4: idempotency records for external side-effects (GitHub commits, email sends). '
    'Check idempotency_key before executing; insert immediately after success. '
    'TTL cleanup after 30 days via nightly scheduler job.';
