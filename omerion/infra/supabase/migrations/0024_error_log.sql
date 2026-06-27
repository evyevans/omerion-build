-- 0024_error_log.sql
-- Non-run error log: bot disconnects, scheduler crashes, webhook failures.
-- Agent-run errors stay in agent_runs.status='error'.
-- Feeds: dashboard Error panel (Live / 24h / 7d / 30d filter).

CREATE TABLE IF NOT EXISTS error_log (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source      text        NOT NULL,   -- 'omerion_bot' | 'scheduler' | 'webhook' | 'discord_route'
    message     text        NOT NULL,
    traceback   text,
    meta        jsonb       NOT NULL DEFAULT '{}',
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_error_log_occurred_at  ON error_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_error_log_source       ON error_log (source);
