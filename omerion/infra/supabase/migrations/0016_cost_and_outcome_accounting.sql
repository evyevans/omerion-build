-- 0016_cost_and_outcome_accounting.sql
-- Phase D: Cost + outcome accounting per Boris+Elon B3/B4 recommendation.
-- Per-run cost columns on agent_runs + a business_outcomes table that ties
-- "money/meeting facts" back to the run that produced them via correlation_id.
-- Mission Control view aggregates the three numbers Elon demanded:
--   outcomes today, error count, total cost.

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS completion_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS llm_cost_usd   NUMERIC(10,6) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tool_call_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS business_outcomes (
    outcome_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    outcome_type    TEXT NOT NULL,
    -- The run that produced this outcome. Nullable because some outcomes
    -- (e.g. inbound replies) arrive via webhook before any run row exists.
    run_id          UUID REFERENCES agent_runs(run_id) ON DELETE SET NULL,
    correlation_id  TEXT,
    contact_id      UUID,
    account_id      UUID,
    opportunity_id  UUID,
    value_usd       NUMERIC(12,2),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT business_outcomes_type_chk CHECK (
        outcome_type IN (
            'qualified_lead', 'booked_demo', 'proposal_sent',
            'signed_contract', 'closed_won', 'closed_lost',
            'reply_received', 'meeting_completed'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_business_outcomes_type_time
    ON business_outcomes (outcome_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_business_outcomes_run
    ON business_outcomes (run_id);
CREATE INDEX IF NOT EXISTS idx_business_outcomes_correlation
    ON business_outcomes (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- Mission Control: 3-row summary the dashboard reads to answer "is the system
-- working today?" without scanning agent_runs / agent_telemetry directly.
CREATE OR REPLACE VIEW mission_control_today AS
WITH today_runs AS (
    SELECT *
    FROM agent_runs
    WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')
)
SELECT
    (SELECT COUNT(*) FROM business_outcomes
     WHERE occurred_at >= date_trunc('day', now() AT TIME ZONE 'UTC')) AS outcomes_today,
    (SELECT COUNT(*) FROM today_runs WHERE status = 'failed')          AS errors_today,
    (SELECT COALESCE(SUM(llm_cost_usd), 0) FROM today_runs)            AS cost_usd_today,
    (SELECT COUNT(*) FROM today_runs WHERE status = 'completed')       AS completed_today,
    (SELECT COUNT(*) FROM today_runs WHERE status = 'running')         AS in_flight_now,
    (SELECT COUNT(*) FROM today_runs WHERE status = 'hitl_waiting')    AS hitl_waiting_now;

-- Add to realtime publication so the dashboard sees outcomes as they arrive.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'public'
          AND tablename = 'business_outcomes'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE business_outcomes;
    END IF;
END$$;
