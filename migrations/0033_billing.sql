-- 0033_billing.sql
-- Revenue and billing foundation: invoices and revenue_events tables.

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id        UUID           PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_slug       TEXT           NOT NULL REFERENCES clients(client_slug),
    amount_usd        NUMERIC(10, 2) NOT NULL,
    status            TEXT           NOT NULL DEFAULT 'draft',
    due_date          DATE,
    paid_at           TIMESTAMPTZ,
    stripe_invoice_id TEXT,
    line_items        JSONB          NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_invoices_client ON invoices (client_slug);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices (status);

CREATE TABLE IF NOT EXISTS revenue_events (
    event_id        UUID           PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_slug     TEXT           REFERENCES clients(client_slug),
    event_type      TEXT           NOT NULL,
    amount_usd      NUMERIC(10, 2) NOT NULL,
    stripe_event_id TEXT           UNIQUE,
    occurred_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
    meta            JSONB
);

CREATE INDEX IF NOT EXISTS idx_revenue_events_client     ON revenue_events (client_slug);
CREATE INDEX IF NOT EXISTS idx_revenue_events_occurred   ON revenue_events (occurred_at);
