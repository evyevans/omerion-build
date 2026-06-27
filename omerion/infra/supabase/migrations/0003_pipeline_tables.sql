-- ════════════════════════════════════════════════════════════════════
-- 0003 — Pipeline: scores, opportunities, blueprints, build, measurement
-- ════════════════════════════════════════════════════════════════════

-- ─── scores (owned by: ICP Scoring #6) ─────────────────────────────
CREATE TABLE scores (
    score_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id    UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    fit_score     NUMERIC(5,4) NOT NULL,
    intent_score  NUMERIC(5,4) NOT NULL,
    timing_score  NUMERIC(5,4) NOT NULL,
    final_score   NUMERIC(5,4) NOT NULL,
    segment       score_segment NOT NULL,
    rationale     TEXT,
    recommended_action TEXT,
    run_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    weights_snapshot JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- latest-per-contact-per-day
    UNIQUE (contact_id, run_date)
);
CREATE INDEX idx_scores_final_run ON scores (run_date DESC, final_score DESC);
CREATE INDEX idx_scores_segment ON scores (segment, run_date DESC);

-- ─── opportunities (owned by: ICP Scoring #6 / Offer Matching #7) ──
CREATE TABLE opportunities (
    opportunity_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id       UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    account_id       UUID REFERENCES accounts(account_id) ON DELETE SET NULL,
    stage            opportunity_stage NOT NULL DEFAULT 'new_lead',
    offer_memo_url   TEXT,
    offer_modules    TEXT[] DEFAULT '{}',    -- e.g. ['DAAM','ORIA']
    offer_tier       TEXT,                   -- 'starter' | 'growth' | 'enterprise'
    value_est_usd    NUMERIC(12,2),
    pricing_band     JSONB,
    open_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    close_date       DATE,
    won_amount_usd   NUMERIC(12,2),
    lost_reason      TEXT,
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_opportunities_stage ON opportunities (stage);
CREATE INDEX idx_opportunities_contact ON opportunities (contact_id);

-- ─── blueprints (owned by: Meeting Intelligence #8) ────────────────
CREATE TABLE blueprints (
    blueprint_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    opportunity_id   UUID REFERENCES opportunities(opportunity_id) ON DELETE CASCADE,
    meeting_id       TEXT,       -- external Fireflies meeting_id
    transcript_url   TEXT,
    w5h              JSONB NOT NULL,  -- {who, what, where, when, why, how}
    constraints      JSONB DEFAULT '{}'::jsonb,
    architecture_md  TEXT,        -- markdown blueprint doc
    blueprint_url    TEXT,        -- Drive/Notion link
    backlog          JSONB NOT NULL DEFAULT '[]'::jsonb, -- [{task_name, module, priority, ...}]
    hitl_flags       JSONB DEFAULT '[]'::jsonb,
    status           TEXT NOT NULL DEFAULT 'draft',  -- draft | approved | rejected | shipped
    founder_approved_at TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_blueprints_opp ON blueprints (opportunity_id);
CREATE INDEX idx_blueprints_status ON blueprints (status);

-- ─── build_tasks (owned by: Build Orchestrator #9) ─────────────────
CREATE TABLE build_tasks (
    task_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    blueprint_id    UUID NOT NULL REFERENCES blueprints(blueprint_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    module          TEXT NOT NULL,   -- DAAM | ORIA | RORA | ASAP | internal
    spec_md         TEXT NOT NULL,
    spec_url        TEXT,
    acceptance_criteria JSONB DEFAULT '[]'::jsonb,
    branch          TEXT,
    pr_url          TEXT,
    github_issue_number INT,
    ci_status       TEXT,
    status          build_task_status NOT NULL DEFAULT 'pending',
    priority        INT DEFAULT 100,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_build_tasks_blueprint ON build_tasks (blueprint_id);
CREATE INDEX idx_build_tasks_status ON build_tasks (status, priority);

-- ─── deployments (owned by: Build Orchestrator #9) ─────────────────
CREATE TABLE deployments (
    deployment_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    blueprint_id    UUID REFERENCES blueprints(blueprint_id) ON DELETE SET NULL,
    opportunity_id  UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    client_id       UUID,   -- future clients table
    modules_deployed TEXT[] NOT NULL,
    go_live_date    TIMESTAMPTZ NOT NULL,
    status          deployment_status NOT NULL DEFAULT 'queued',
    rollback_to     UUID REFERENCES deployments(deployment_id),
    release_notes   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_deployments_status ON deployments (status);
CREATE INDEX idx_deployments_go_live ON deployments (go_live_date DESC);

-- ─── revenue_events (owned by: Outcome Attribution #10) ────────────
CREATE TABLE revenue_events (
    revenue_event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id        UUID,
    opportunity_id   UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    event_type       TEXT NOT NULL,   -- closed_won | expansion | churn | renewal
    amount_usd       NUMERIC(12,2) NOT NULL,
    occurred_at      TIMESTAMPTZ NOT NULL,
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_revenue_events_client ON revenue_events (client_id, occurred_at DESC);

-- ─── lead_conversions (owned by: Outcome Attribution #10) ──────────
CREATE TABLE lead_conversions (
    conversion_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id       UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    opportunity_id   UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    from_stage       opportunity_stage,
    to_stage         opportunity_stage,
    converted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_lead_conversions_contact ON lead_conversions (contact_id, converted_at DESC);

-- ─── agent_actions (audit trail — owned by: all agents) ────────────
CREATE TABLE agent_actions (
    action_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name     TEXT NOT NULL,
    action_type    TEXT NOT NULL,
    target_type    TEXT,       -- 'contact' | 'account' | 'opportunity' | etc.
    target_id      UUID,
    client_id      UUID,
    outcome_flag   TEXT,       -- 'success' | 'failure' | 'skipped'
    cost_usd       NUMERIC(8,4),
    payload        JSONB DEFAULT '{}'::jsonb,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_actions_agent_time ON agent_actions (agent_name, occurred_at DESC);
CREATE INDEX idx_agent_actions_target ON agent_actions (target_type, target_id);

-- ─── attribution_reports (owned by: Outcome Attribution #10) ───────
CREATE TABLE attribution_reports (
    report_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deployment_id    UUID REFERENCES deployments(deployment_id) ON DELETE CASCADE,
    client_id        UUID,
    kpi_deltas       JSONB NOT NULL,
    summary          TEXT,
    proof_point      TEXT,
    attribution_model TEXT NOT NULL,
    window_days      INT NOT NULL,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_attribution_reports_deployment ON attribution_reports (deployment_id);
