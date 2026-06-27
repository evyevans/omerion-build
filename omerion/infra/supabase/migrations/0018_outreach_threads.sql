-- ════════════════════════════════════════════════════════════════════
-- 0018 — RAG Traction System: outreach_threads cross-channel tracking
-- ════════════════════════════════════════════════════════════════════
--
-- One row per contact. Tracks all cross-channel touch counts, response
-- detection, and ghost escalation state. Written by REACH + NURTURE on
-- every send. Read by the ghost_detector (daily) and response tracker
-- (every 2h).
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS outreach_threads (
    thread_id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id                  UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,

    -- Lifecycle timestamps
    first_touch_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_touch_at               TIMESTAMPTZ,

    -- Per-channel touch counts (maintained by application layer)
    touch_count_email           INT NOT NULL DEFAULT 0,
    touch_count_sms             INT NOT NULL DEFAULT 0,
    touch_count_linkedin        INT NOT NULL DEFAULT 0,
    touch_count_total           INT NOT NULL DEFAULT 0,

    -- Response tracking
    response_received           BOOLEAN NOT NULL DEFAULT false,
    response_at                 TIMESTAMPTZ,
    response_channel            outreach_channel,   -- uses existing enum from 0001

    -- Ghost tracking
    ghost_declared              BOOLEAN NOT NULL DEFAULT false,
    ghost_declared_at           TIMESTAMPTZ,
    ghost_outcome               TEXT,               -- 're_engage' | 'escalate_to_hitl' | 'archive'
    reengagement_scheduled_at   TIMESTAMPTZ,
    reengagement_strategy       TEXT,               -- 'switch_channel' | 'founder_personal' | 'do_not_contact'

    metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One thread per contact; upsert on contact_id conflict.
    UNIQUE (contact_id)
);

-- Efficient polling by ghost_detector (scans only unresolved threads).
CREATE INDEX IF NOT EXISTS idx_outreach_threads_ghost_scan
    ON outreach_threads (last_touch_at, touch_count_total)
    WHERE ghost_declared = false AND response_received = false;

-- Efficient polling by response_tracker (already-responded are excluded).
CREATE INDEX IF NOT EXISTS idx_outreach_threads_contact
    ON outreach_threads (contact_id);

-- Enable realtime for dashboard.
ALTER PUBLICATION supabase_realtime ADD TABLE outreach_threads;
