-- ════════════════════════════════════════════════════════════════════
-- 0022 — properties: HomeHarvest (Realtor.com) ingest table
-- ════════════════════════════════════════════════════════════════════
-- Stores normalized real estate listings scraped via the homeharvest
-- Python library (https://github.com/ZacharyHampton/HomeHarvest).
-- Used by SCOUT (lead_scraper_enricher) to surface properties for
-- downstream ICP scoring, dossier generation, and outreach targeting.

DO $$ BEGIN
    CREATE TYPE property_listing_status AS ENUM (
        'for_sale',
        'for_rent',
        'sold',
        'pending'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS properties (
    property_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source             TEXT NOT NULL DEFAULT 'realtor.com',
    external_id        TEXT NOT NULL,                       -- HomeHarvest property_id / mls_id
    listing_status     property_listing_status NOT NULL,
    address            TEXT NOT NULL DEFAULT '',
    city               TEXT NOT NULL DEFAULT '',
    state              TEXT NOT NULL DEFAULT '',
    zip_code           TEXT NOT NULL DEFAULT '',
    list_price         NUMERIC(14,2),
    sold_price         NUMERIC(14,2),
    beds               INTEGER,
    baths_full         INTEGER,
    baths_half         INTEGER,
    sqft               INTEGER,
    lot_sqft           INTEGER,
    year_built         INTEGER,
    style              TEXT NOT NULL DEFAULT '',            -- single_family | condo | townhouse | ...
    list_date          DATE,
    sold_date          DATE,
    days_on_market     INTEGER,
    primary_photo_url  TEXT NOT NULL DEFAULT '',
    listing_url        TEXT NOT NULL DEFAULT '',
    agent_name         TEXT NOT NULL DEFAULT '',
    agent_email        TEXT NOT NULL DEFAULT '',
    agent_phone        TEXT NOT NULL DEFAULT '',
    broker_name        TEXT NOT NULL DEFAULT '',
    raw                JSONB NOT NULL DEFAULT '{}'::jsonb,  -- full HomeHarvest row preserved
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_properties_status_city
    ON properties (listing_status, city);
CREATE INDEX IF NOT EXISTS idx_properties_list_date
    ON properties (list_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_properties_agent_email
    ON properties (agent_email)
    WHERE agent_email <> '';
CREATE INDEX IF NOT EXISTS idx_properties_zip
    ON properties (zip_code);
