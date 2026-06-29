-- 0068: Website-facing tables: blueprint_requests, waitlist_entries, video_urls
-- These power the Omerion marketing site (Agentic Factory flow, waitlist capture, video management).

-- ── blueprint_requests ───────────────────────────────────────────────────────
-- Stores free diagnosis form submissions and paid blueprint requests from the website.
CREATE TABLE IF NOT EXISTS blueprint_requests (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      TEXT UNIQUE NOT NULL,
    email           TEXT,
    client_name     TEXT,
    business_name   TEXT,
    form_data       JSONB NOT NULL DEFAULT '{}',
    diagnosis_data  JSONB,           -- {processes, hrs_wk, roi_mo, opportunities[]}
    blueprint_html  TEXT,            -- populated by paid blueprint generation
    status          TEXT NOT NULL DEFAULT 'pending',
    -- pending | completed | approved | regenerating
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_blueprint_requests_session ON blueprint_requests (session_id);
CREATE INDEX IF NOT EXISTS idx_blueprint_requests_status  ON blueprint_requests (status);
CREATE INDEX IF NOT EXISTS idx_blueprint_requests_email   ON blueprint_requests (email);

-- ── waitlist_entries ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS waitlist_entries (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email      TEXT UNIQUE NOT NULL,
    source     TEXT NOT NULL DEFAULT 'hero_waitlist',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── video_urls ───────────────────────────────────────────────────────────────
-- Allows admin to swap demo videos per agent key without redeploying.
CREATE TABLE IF NOT EXISTS video_urls (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_key  TEXT UNIQUE NOT NULL,
    url        TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
