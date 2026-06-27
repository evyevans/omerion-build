-- 0013_agent_runs.sql
-- Phase 9: durable agent run lifecycle.
--
-- The system previously had no single source of truth for "is this run still
-- going? did it succeed?" — lifecycle was implicit in `checkpoints` (graph
-- state) + `agent_telemetry` (per-node spans). This table closes that gap so
-- founder visibility, Discord completion callbacks, and dashboard "running
-- now" feeds all read from one place.
--
-- `thread_id` is the LangGraph PostgresSaver thread key (= run_id::text by
-- convention); `review_id` is set when the graph is paused on a HITL gate.

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name          TEXT        NOT NULL,
    thread_id           TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','hitl_waiting','completed','failed','cancelled')),
    source_channel      TEXT        NOT NULL
        CHECK (source_channel IN ('discord','openclaw','scheduler','api')),
    triggered_by        TEXT,
    inputs              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    discord_channel_id  TEXT,
    discord_thread_id   TEXT,
    correlation_id      UUID,
    review_id           UUID        REFERENCES founder_review_queue(review_id) ON DELETE SET NULL,
    result_summary      TEXT,
    error               TEXT,
    cost_usd            NUMERIC(10,4),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS agent_runs_status_idx
    ON agent_runs (status);
CREATE INDEX IF NOT EXISTS agent_runs_agent_idx
    ON agent_runs (agent_name, created_at DESC);
CREATE INDEX IF NOT EXISTS agent_runs_thread_idx
    ON agent_runs (thread_id);
CREATE INDEX IF NOT EXISTS agent_runs_review_idx
    ON agent_runs (review_id)
    WHERE review_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_runs_correlation_idx
    ON agent_runs (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- Dashboard subscribes via Supabase realtime to drive the "running now" feed.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime' AND tablename = 'agent_runs'
    ) THEN
        EXECUTE 'ALTER PUBLICATION supabase_realtime ADD TABLE agent_runs';
    END IF;
END $$;
