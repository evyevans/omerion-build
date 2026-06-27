-- 0028_agent_runs_universal.sql
-- Ensures `agent_runs` and related tables have the columns the
-- universal runtime writes. Idempotent.

BEGIN;

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_slug     TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    department      TEXT,
    correlation_id  TEXT,
    success         BOOLEAN NOT NULL DEFAULT TRUE,
    confidence      NUMERIC(4,3),
    needs_hitl      BOOLEAN NOT NULL DEFAULT FALSE,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(12,6) NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    result          JSONB NOT NULL DEFAULT '{}'::jsonb,
    rationale       TEXT,
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_client_started
  ON agent_runs(client_slug, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_started
  ON agent_runs(agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_correlation
  ON agent_runs(correlation_id);

CREATE TABLE IF NOT EXISTS events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            TEXT NOT NULL,
    client_slug     TEXT NOT NULL,
    correlation_id  TEXT,
    source_agent    TEXT,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    emitted_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_client_emitted
  ON events(client_slug, emitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type
  ON events(type);

CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_slug     TEXT NOT NULL,
    competitor      TEXT NOT NULL,
    url             TEXT NOT NULL,
    text            TEXT NOT NULL,
    hash            TEXT NOT NULL,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_competitor_snapshots_lookup
  ON competitor_snapshots(client_slug, competitor, captured_at DESC);

COMMIT;
