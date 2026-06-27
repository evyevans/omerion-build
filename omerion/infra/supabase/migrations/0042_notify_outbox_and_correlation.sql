-- Migration 0042 — Wave 3.3 notification_outbox + Wave 3.4 correlation IDs.
--
-- Two changes bundled because they ship together with the sweeper and the
-- Mission Control alert work:
--
-- 1. notification_outbox — durable retry queue for Discord webhooks
--    (HITL alerts, run completion pings, error alerts). Replaces the
--    legacy silent-swallow path at notifications/hitl.py.
--
-- 2. agent_messages.correlation_id — promotes correlation_id from a JSONB
--    field buried in `meta` to a proper indexed column so the dashboard
--    can render an end-to-end chain query in a single fast lookup.
--    `agent_runs.correlation_id` already exists (migration 0013).
--
-- Both changes are idempotent.

BEGIN;

-- ─── Wave 3.3: notification_outbox ────────────────────────────────────
CREATE TABLE IF NOT EXISTS notification_outbox (
    outbox_id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    notification_type  TEXT         NOT NULL
        CHECK (notification_type IN (
            'hitl_review', 'run_completion', 'error_alert',
            'cost_spike', 'sweeper_summary'
        )),
    target_id          TEXT         NOT NULL,     -- review_id, run_id, etc.
    webhook_url        TEXT         NOT NULL,
    payload            JSONB        NOT NULL DEFAULT '{}'::jsonb,

    -- Retry bookkeeping.
    status             TEXT         NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'permanent_failure', 'abandoned')),
    attempt_count      INT          NOT NULL DEFAULT 0,
    max_attempts       INT          NOT NULL DEFAULT 4,
    last_error         TEXT,
    next_retry_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),

    -- Idempotency on (type, target). Sweeper relies on this to prevent
    -- duplicate alerts for the same review or run.
    idempotency_key    TEXT,

    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    delivered_at       TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS notification_outbox_idempotency_uidx
    ON notification_outbox (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS notification_outbox_ready_idx
    ON notification_outbox (status, next_retry_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS notification_outbox_type_idx
    ON notification_outbox (notification_type, created_at DESC);

COMMENT ON TABLE notification_outbox IS
    'Wave 3.3: durable retry queue for Discord webhook deliveries. Sweeper retries pending rows; abandoned rows surface to the operator via Mission Control.';

-- ─── Wave 3.4: correlation_id on agent_messages ───────────────────────
ALTER TABLE agent_messages
    ADD COLUMN IF NOT EXISTS correlation_id UUID;

CREATE INDEX IF NOT EXISTS agent_messages_correlation_idx
    ON agent_messages (correlation_id)
    WHERE correlation_id IS NOT NULL;

COMMENT ON COLUMN agent_messages.correlation_id IS
    'Wave 3.4: end-to-end chain identifier. Same UUID flows from trigger → wrapper → emit → downstream wrapper. Dashboard joins on this column for the per-business-chain timeline view.';

COMMIT;
