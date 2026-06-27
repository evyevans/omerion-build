-- 0014_telemetry_correlation_id.sql
-- Phase 9: stamp `agent_telemetry` rows with the same correlation_id that
-- the run lifecycle, events bus, HITL queue, and OpenClaw audit log all
-- already carry. Lets a single id join across every per-node span, every
-- emitted event, and the agent_runs row for one execution.

ALTER TABLE agent_telemetry
    ADD COLUMN IF NOT EXISTS correlation_id UUID;

CREATE INDEX IF NOT EXISTS idx_telemetry_correlation
    ON agent_telemetry (correlation_id)
    WHERE correlation_id IS NOT NULL;
