-- agent_run_registry: tracks R1/R2/R3 weekly run status for coordination
-- Used by agent_coordinator.py to trigger R3 event-driven when R1+R2 are done.

CREATE TABLE IF NOT EXISTS agent_run_registry (
    id            BIGSERIAL PRIMARY KEY,
    agent_id      TEXT        NOT NULL,
    week_number   SMALLINT    NOT NULL,  -- ISO week (1-53)
    year          SMALLINT    NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending'  -- pending | running | complete | failed
                  CHECK (status IN ('pending', 'running', 'complete', 'failed')),
    completed_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_id, week_number, year)
);

CREATE INDEX IF NOT EXISTS idx_agent_run_registry_week
    ON agent_run_registry (week_number, year);

COMMENT ON TABLE agent_run_registry IS
    'Tracks R1/R2/R3 weekly runs for the R3 coordination gate. '
    'R3 fires event-driven when both r1-market-tech-watcher and r2-oss-scout show status=complete.';
