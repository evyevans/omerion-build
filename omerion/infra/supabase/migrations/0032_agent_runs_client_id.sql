-- 0032_agent_runs_client_id.sql
-- Add client_id to agent_runs for per-client health scoring in client_success agent.
-- Without this column, compute_health() catches a PGRST error and returns runs_7d=0
-- for every client, permanently red-banding all clients and triggering a churn event storm.

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES clients(client_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS agent_runs_client_idx
    ON agent_runs (client_id, created_at DESC)
    WHERE client_id IS NOT NULL;
