-- 0032_deal_pipeline.sql
-- Adds deal pipeline stage tracking to the clients table and a milestones table.

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS deal_stage            TEXT NOT NULL DEFAULT 'Discovery',
    ADD COLUMN IF NOT EXISTS deal_stage_updated_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS milestones (
    milestone_id UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_slug  TEXT        NOT NULL REFERENCES clients(client_slug) ON DELETE CASCADE,
    title        TEXT        NOT NULL,
    due_date     DATE,
    completed_at TIMESTAMPTZ,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_milestones_client ON milestones (client_slug);
