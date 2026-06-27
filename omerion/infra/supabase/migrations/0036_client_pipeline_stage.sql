-- 0036_client_pipeline_stage.sql
-- Post-sale delivery pipeline tracking on the clients table.
--
-- The `opportunities` table already tracks pre-sale stages via
-- `opportunity_stage` (new_lead → contacted → ... → won/lost). This
-- migration adds the post-sale delivery lifecycle on `clients`:
--   signed → kickoff → in_delivery → success → renewal_due → churned
-- plus an append-only `client_stage_history` so funnel-conversion and
-- average-time-in-stage reports can be built without joining mutated rows.

DO $$ BEGIN
    CREATE TYPE client_pipeline_stage AS ENUM (
        'signed',
        'kickoff',
        'in_delivery',
        'success',
        'renewal_due',
        'churned'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS pipeline_stage client_pipeline_stage
        NOT NULL DEFAULT 'signed';

CREATE INDEX IF NOT EXISTS idx_clients_pipeline_stage
    ON clients (pipeline_stage);

CREATE TABLE IF NOT EXISTS client_stage_history (
    id          BIGSERIAL PRIMARY KEY,
    client_id   UUID NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    from_stage  client_pipeline_stage,
    to_stage    client_pipeline_stage NOT NULL,
    changed_by  TEXT,
    notes       TEXT,
    meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_client_stage_history_client
    ON client_stage_history (client_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_client_stage_history_to_stage
    ON client_stage_history (to_stage, created_at DESC);

-- Trigger: auto-write history row on every pipeline_stage change.
-- The trigger fires BEFORE UPDATE so we capture the prior value.
CREATE OR REPLACE FUNCTION _log_client_pipeline_stage_change()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.pipeline_stage IS DISTINCT FROM OLD.pipeline_stage THEN
        INSERT INTO client_stage_history (client_id, from_stage, to_stage)
        VALUES (NEW.client_id, OLD.pipeline_stage, NEW.pipeline_stage);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_log_client_pipeline_stage_change ON clients;
CREATE TRIGGER trg_log_client_pipeline_stage_change
    BEFORE UPDATE ON clients
    FOR EACH ROW
    EXECUTE FUNCTION _log_client_pipeline_stage_change();
