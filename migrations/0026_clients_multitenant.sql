-- 0026_clients_multitenant.sql
-- Adds multi-tenant + industry-pack fields to clients table.
-- Idempotent: safe to run repeatedly.

BEGIN;

CREATE TABLE IF NOT EXISTS clients (
    client_slug    TEXT PRIMARY KEY,
    display_name   TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS industry TEXT,
  ADD COLUMN IF NOT EXISTS config   JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS secrets_ref TEXT,
  ADD COLUMN IF NOT EXISTS departments_enabled TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  ADD COLUMN IF NOT EXISTS active   BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_clients_industry ON clients(industry);
CREATE INDEX IF NOT EXISTS idx_clients_active   ON clients(active);

-- Seed Omerion's own row if missing.
INSERT INTO clients (client_slug, display_name, industry, departments_enabled)
VALUES ('omerion-internal', 'Omerion (internal)', 'ai_automation_agency',
        ARRAY['agentic_factory','lead_gen','research_intelligence',
              'client_delivery','recursive_self_improvement'])
ON CONFLICT (client_slug) DO NOTHING;

COMMIT;
