-- 0031_competitor_battle_cards.sql
-- Competitor intelligence battle cards table for competitive_intel agent.
-- Without this table, upsert_battle_card() fails silently on every run.

CREATE TABLE IF NOT EXISTS competitor_battle_cards (
    signal_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'other',
    title        TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    url          TEXT UNIQUE,
    impact       TEXT NOT NULL DEFAULT 'low',
    published_at TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS battle_cards_competitor_idx
    ON competitor_battle_cards (competitor, created_at DESC);

CREATE INDEX IF NOT EXISTS battle_cards_impact_idx
    ON competitor_battle_cards (impact)
    WHERE impact IN ('medium', 'high');
