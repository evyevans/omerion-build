-- ════════════════════════════════════════════════════════════════════
-- 0002 — Core tables: markets, accounts, contacts, events
-- Data spine shared by all 14 agents.
-- ════════════════════════════════════════════════════════════════════

-- ─── markets (owned by: Market Mapper #1) ──────────────────────────
CREATE TABLE markets (
    market_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name             TEXT NOT NULL UNIQUE,
    geo              geography(Polygon, 4326),
    bounding_box     geometry(Polygon, 4326),
    tier_label       account_tier DEFAULT 'tier_1',
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_markets_geo ON markets USING GIST (geo);

-- ─── accounts (owned by: Market Mapper #1) ─────────────────────────
CREATE TABLE accounts (
    account_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                  TEXT NOT NULL,
    domain                TEXT,
    website               TEXT,
    linkedin_company_url  TEXT,
    persona               persona NOT NULL,
    tier                  account_tier DEFAULT 'tier_1',
    status                account_status NOT NULL DEFAULT 'new',
    market_id             UUID REFERENCES markets(market_id) ON DELETE SET NULL,
    volume_bucket         TEXT,
    team_size_bucket      TEXT,
    tech_maturity_signals JSONB DEFAULT '[]'::jsonb,
    score                 NUMERIC(5,4) DEFAULT 0,
    confidence            NUMERIC(5,4) DEFAULT 0,
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_refreshed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata              JSONB DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- idempotency for Market Mapper re-runs
    UNIQUE (domain, market_id)
);
CREATE INDEX idx_accounts_persona_tier ON accounts (persona, tier);
CREATE INDEX idx_accounts_status ON accounts (status);
CREATE INDEX idx_accounts_score ON accounts (score DESC);
CREATE INDEX idx_accounts_domain_trgm ON accounts USING GIN (domain gin_trgm_ops);

-- ─── contacts (owned by: Lead Scraper & Enricher #3) ───────────────
CREATE TABLE contacts (
    contact_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id            UUID REFERENCES accounts(account_id) ON DELETE CASCADE,
    first_name            TEXT,
    last_name             TEXT,
    email                 TEXT,
    phone                 TEXT,
    linkedin_url          TEXT,
    role                  TEXT,
    persona               persona NOT NULL,
    problem_hypothesis    TEXT,
    status                contact_status NOT NULL DEFAULT 'new',
    do_not_contact        BOOLEAN NOT NULL DEFAULT false,
    opt_out_email         BOOLEAN NOT NULL DEFAULT false,
    opt_out_sms           BOOLEAN NOT NULL DEFAULT false,
    opt_out_linkedin      BOOLEAN NOT NULL DEFAULT false,
    enrichment_confidence JSONB DEFAULT '{}'::jsonb,
    founder_priority      BOOLEAN NOT NULL DEFAULT false,
    tags                  TEXT[] DEFAULT '{}',
    last_touch_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- dedup key: email is primary, fall back to linkedin
    UNIQUE (email),
    UNIQUE (linkedin_url)
);
CREATE INDEX idx_contacts_account ON contacts (account_id);
CREATE INDEX idx_contacts_persona_status ON contacts (persona, status);
CREATE INDEX idx_contacts_priority ON contacts (founder_priority) WHERE founder_priority = true;

-- ─── events (universal event bus — owned by: all agents) ───────────
-- Canonical event stream from blueprint §9.2.
CREATE TABLE events (
    event_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type         TEXT NOT NULL,          -- e.g. 'contact.enriched', 'blueprint.approved'
    source_agent TEXT NOT NULL,          -- e.g. 'lead_scraper_enricher'
    contact_id   UUID REFERENCES contacts(contact_id) ON DELETE SET NULL,
    account_id   UUID REFERENCES accounts(account_id) ON DELETE SET NULL,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    correlation_id UUID,                  -- links related events across agents
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_events_type_time ON events (type, created_at DESC);
CREATE INDEX idx_events_contact ON events (contact_id, created_at DESC);
CREATE INDEX idx_events_account ON events (account_id, created_at DESC);
CREATE INDEX idx_events_correlation ON events (correlation_id);

-- Realtime replication for event-driven agent triggers
ALTER PUBLICATION supabase_realtime ADD TABLE events;
