-- ════════════════════════════════════════════════════════════════════
-- 0004 — Operational tables: outreach, research, HITL queue
-- ════════════════════════════════════════════════════════════════════

-- ─── outbound_communications (owned by: LinkedIn #4 / CRM Nurture #5) ─
CREATE TABLE outbound_communications (
    comm_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id     UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    channel        outreach_channel NOT NULL,
    direction      outreach_direction NOT NULL DEFAULT 'outbound',
    sequence_id    UUID,
    sequence_step  INT,
    template_key   TEXT,
    subject        TEXT,
    body           TEXT NOT NULL,
    sent_at        TIMESTAMPTZ,
    delivered_at   TIMESTAMPTZ,
    opened_at      TIMESTAMPTZ,
    clicked_at     TIMESTAMPTZ,
    replied_at     TIMESTAMPTZ,
    bounced_at     TIMESTAMPTZ,
    provider_id    TEXT,   -- Twilio SID, Gmail Message-ID, LinkedIn activity ID
    status         TEXT NOT NULL DEFAULT 'queued',
    idempotency_key UUID NOT NULL UNIQUE,
    error_detail   JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_comms_contact ON outbound_communications (contact_id, sent_at DESC);
CREATE INDEX idx_comms_channel_status ON outbound_communications (channel, status);

-- ─── nurture_sequences (owned by: CRM Nurture #5) ──────────────────
CREATE TABLE nurture_sequences (
    sequence_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id     UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    stage          opportunity_stage NOT NULL,
    persona        persona NOT NULL,
    template_chain TEXT[] NOT NULL,
    current_step   INT NOT NULL DEFAULT 0,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_touch_at  TIMESTAMPTZ,
    paused_reason  TEXT,
    status         TEXT NOT NULL DEFAULT 'active', -- active | paused | completed | stopped
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (contact_id, stage)
);
CREATE INDEX idx_nurture_sequences_status ON nurture_sequences (status);

-- ─── contact_activity_log (owned by: CRM Nurture #5 / LinkedIn #4) ──
CREATE TABLE contact_activity_log (
    activity_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id     UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    activity_type  TEXT NOT NULL,   -- email_open | link_click | sms_delivered | linkedin_view | ...
    channel        outreach_channel,
    comm_id        UUID REFERENCES outbound_communications(comm_id) ON DELETE SET NULL,
    tracking_id    TEXT,
    metadata       JSONB DEFAULT '{}'::jsonb,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_activity_contact_time ON contact_activity_log (contact_id, occurred_at DESC);
CREATE INDEX idx_activity_type_time ON contact_activity_log (activity_type, occurred_at DESC);

-- ─── research_dossiers (owned by: High-Quality Lead Scraping #2) ───
CREATE TABLE research_dossiers (
    dossier_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id     UUID REFERENCES contacts(contact_id) ON DELETE CASCADE,
    account_id     UUID REFERENCES accounts(account_id) ON DELETE CASCADE,
    summary        TEXT NOT NULL,
    source_urls    JSONB NOT NULL DEFAULT '[]'::jsonb,
    pain_signals   JSONB NOT NULL DEFAULT '[]'::jsonb,
    outreach_angles JSONB NOT NULL DEFAULT '[]'::jsonb,
    conversation_hooks JSONB DEFAULT '[]'::jsonb,
    offer_match    JSONB,   -- suggested DAAM/ORIA/RORA/ASAP combo
    confidence_score NUMERIC(5,4) DEFAULT 0,
    founder_approved BOOLEAN DEFAULT false,
    pinecone_ids   TEXT[] DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_dossiers_account ON research_dossiers (account_id);
CREATE INDEX idx_dossiers_contact ON research_dossiers (contact_id);

-- ─── generated_drafts (owned by: any agent that drafts copy) ───────
-- Corpus of every AI-generated draft (approved or not). Feeds R&D.
CREATE TABLE generated_drafts (
    draft_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name     TEXT NOT NULL,
    contact_id     UUID REFERENCES contacts(contact_id) ON DELETE SET NULL,
    opportunity_id UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    purpose        TEXT NOT NULL,   -- 'outreach_email' | 'sms' | 'blueprint' | 'offer_memo' | ...
    model          TEXT NOT NULL,
    prompt_hash    TEXT,
    draft_body     TEXT NOT NULL,
    draft_metadata JSONB DEFAULT '{}'::jsonb,
    approved       BOOLEAN,
    founder_feedback TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_drafts_agent ON generated_drafts (agent_name, created_at DESC);
CREATE INDEX idx_drafts_purpose_approved ON generated_drafts (purpose, approved);

-- ─── founder_review_queue (owned by: all agents via omerion_core.hitl) ─
CREATE TABLE founder_review_queue (
    review_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name       TEXT NOT NULL,
    session_id       TEXT NOT NULL,    -- OpenClaw session ID
    correlation_id   UUID NOT NULL,
    subject          TEXT NOT NULL,
    context_md       TEXT NOT NULL,    -- all relevant context rendered as markdown
    draft_ref        JSONB NOT NULL,   -- {type, id, url, body_excerpt}
    approve_token    TEXT NOT NULL UNIQUE,  -- cryptographic, one-time
    reject_token     TEXT NOT NULL UNIQUE,
    decision         hitl_decision NOT NULL DEFAULT 'pending',
    decision_notes   TEXT,
    delegated_to     TEXT,
    escalated_at     TIMESTAMPTZ,
    decided_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '48 hours')
);
CREATE INDEX idx_review_queue_decision ON founder_review_queue (decision, created_at DESC);
CREATE INDEX idx_review_queue_session ON founder_review_queue (session_id);
ALTER PUBLICATION supabase_realtime ADD TABLE founder_review_queue;
