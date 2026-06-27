-- Migration 0041 — event_dead_letter (DLQ for failed event dispatches).
--
-- Wave 3.1. When the broker fails to dispatch a downstream agent (handoff
-- log fails, agent crashes mid-spawn, mutex held by a dead worker, etc.)
-- we want the event preserved on disk so a sweeper can replay it later
-- rather than losing it silently.
--
-- The retry loop in `omerion/omerion_core/runtime/sweeper.py` reads rows
-- where `status = 'pending'` AND `attempt_count < max_attempts` AND
-- `next_retry_at <= now()`, attempts the dispatch, and either marks the
-- row `delivered` on success or bumps `attempt_count` + reschedules
-- on failure.
--
-- A row marked `permanent_failure` (attempt_count >= max_attempts or
-- the dispatcher reported a schema/policy failure) is left in place so
-- the operator can inspect it and decide whether to replay manually or
-- ignore.
--
-- Safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS event_dead_letter (
    dlq_id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Identity of the original event.
    event_id        UUID,
    event_type      TEXT         NOT NULL,
    source_agent    TEXT,
    correlation_id  UUID,

    -- The downstream agent that failed to receive the dispatch.
    target_agent    TEXT         NOT NULL,

    -- Original payload for replay. JSONB so we can validate against the
    -- typed schema before re-dispatching.
    payload         JSONB        NOT NULL DEFAULT '{}'::jsonb,

    -- Retry bookkeeping.
    status          TEXT         NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'permanent_failure', 'abandoned')),
    attempt_count   INT          NOT NULL DEFAULT 0,
    max_attempts    INT          NOT NULL DEFAULT 5,
    last_error      TEXT,
    next_retry_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Provenance.
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    delivered_at    TIMESTAMPTZ
);

-- Sweeper reads "ready to retry" rows; index supports that hot path.
CREATE INDEX IF NOT EXISTS event_dead_letter_ready_idx
    ON event_dead_letter (status, next_retry_at)
    WHERE status = 'pending';

-- Operator view: filter by target agent or by event type to triage.
CREATE INDEX IF NOT EXISTS event_dead_letter_target_idx
    ON event_dead_letter (target_agent, created_at DESC);

CREATE INDEX IF NOT EXISTS event_dead_letter_type_idx
    ON event_dead_letter (event_type, created_at DESC);

-- Idempotency: the same (event_id, target_agent) pair only generates one
-- DLQ row. A retry that fails repeatedly bumps attempt_count rather than
-- creating new rows.
CREATE UNIQUE INDEX IF NOT EXISTS event_dead_letter_uidx
    ON event_dead_letter (event_id, target_agent)
    WHERE event_id IS NOT NULL;

COMMENT ON TABLE event_dead_letter IS
    'Wave 3.1: DLQ for failed broker dispatches. Sweeper retries pending rows; permanent_failure rows are left for operator inspection.';

COMMIT;
