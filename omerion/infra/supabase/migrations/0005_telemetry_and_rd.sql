-- ════════════════════════════════════════════════════════════════════
-- 0005 — Telemetry & R&D: agent observability + recursive improvement
-- ════════════════════════════════════════════════════════════════════

-- ─── agent_telemetry (owned by: all agents via omerion_core.telemetry) ─
CREATE TABLE agent_telemetry (
    telemetry_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name       TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    run_id           UUID NOT NULL,
    node_name        TEXT,
    started_at       TIMESTAMPTZ NOT NULL,
    ended_at         TIMESTAMPTZ,
    duration_ms      INT,
    status           TEXT NOT NULL,   -- 'success' | 'failure' | 'hitl_pending' | 'retrying'
    tokens_input     INT DEFAULT 0,
    tokens_output    INT DEFAULT 0,
    cost_usd         NUMERIC(10,6) DEFAULT 0,
    model_used       TEXT,
    hitl_wait_ms     INT DEFAULT 0,
    error            JSONB,
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_telemetry_agent_time ON agent_telemetry (agent_name, started_at DESC);
CREATE INDEX idx_telemetry_session ON agent_telemetry (session_id);
CREATE INDEX idx_telemetry_run ON agent_telemetry (run_id);

-- ─── agent_performance_metrics (owned by: R4) ──────────────────────
-- R4 rolls agent_telemetry into pre-aggregated KPI rows for fast reads
CREATE TABLE agent_performance_metrics (
    metric_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name       TEXT NOT NULL,
    metric_date      DATE NOT NULL,
    runs_total       INT NOT NULL DEFAULT 0,
    runs_success     INT NOT NULL DEFAULT 0,
    runs_failure     INT NOT NULL DEFAULT 0,
    avg_duration_ms  INT,
    p95_duration_ms  INT,
    total_cost_usd   NUMERIC(10,4) DEFAULT 0,
    hitl_rejections  INT NOT NULL DEFAULT 0,
    hitl_approvals   INT NOT NULL DEFAULT 0,
    regression_flags JSONB DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_name, metric_date)
);
CREATE INDEX idx_perf_metrics_date ON agent_performance_metrics (metric_date DESC);

-- ─── api_call_log (owned by: all agents via omerion_core.clients) ──
-- Per-call log for rate-limit and incident forensics
CREATE TABLE api_call_log (
    call_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name     TEXT NOT NULL,
    service        TEXT NOT NULL,   -- 'anthropic' | 'openai' | 'supabase' | 'pinecone' | 'gmail' | ...
    endpoint       TEXT,
    started_at     TIMESTAMPTZ NOT NULL,
    ended_at       TIMESTAMPTZ,
    status_code    INT,
    attempt        INT NOT NULL DEFAULT 1,
    retried_after_ms INT,
    cost_usd       NUMERIC(10,6) DEFAULT 0,
    error_class    TEXT,
    correlation_id UUID,
    metadata       JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX idx_api_log_service_time ON api_call_log (service, started_at DESC);
CREATE INDEX idx_api_log_agent_time ON api_call_log (agent_name, started_at DESC);

-- ─── rd_insights (owned by: R1 Market/Tech Watcher) ────────────────
CREATE TABLE rd_insights (
    insight_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_url       TEXT NOT NULL,
    source_type      TEXT NOT NULL,   -- 'rss' | 'github_release' | 'newsletter' | 'blog' | ...
    title            TEXT NOT NULL,
    summary          TEXT NOT NULL,
    impact_tag       TEXT NOT NULL,   -- 'daam' | 'oria' | 'rora' | 'asap' | 'internal_os'
    estimated_priority TEXT NOT NULL, -- 'high' | 'medium' | 'low'
    raw_content      TEXT,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_by_r3   BOOLEAN NOT NULL DEFAULT false,
    metadata         JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX idx_insights_priority_time ON rd_insights (estimated_priority, ingested_at DESC);
CREATE INDEX idx_insights_impact ON rd_insights (impact_tag, ingested_at DESC);

-- ─── oss_candidates (owned by: R2 OSS Scout) ───────────────────────
CREATE TABLE oss_candidates (
    candidate_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_url         TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    stars            INT,
    last_commit_at   TIMESTAMPTZ,
    license          TEXT,
    architecture_summary TEXT NOT NULL,
    key_components   JSONB DEFAULT '[]'::jsonb,
    rubric_scores    JSONB NOT NULL,  -- {architecture_quality, complexity, community_velocity, ...}
    integration_type TEXT NOT NULL,   -- 'component' | 'pattern' | 'full_module' | 'reference_only'
    integration_recommendation TEXT,
    risk_notes       TEXT,
    evaluated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_by_r3   BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX idx_oss_candidates_recent ON oss_candidates (evaluated_at DESC);

-- ─── rd_proposals (owned by: R3 Strategic Architect) ───────────────
CREATE TABLE rd_proposals (
    proposal_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title              TEXT NOT NULL,
    problem            TEXT NOT NULL,
    proposed_change    TEXT NOT NULL,
    affected_modules   TEXT[] NOT NULL,
    breaking_changes   TEXT,
    test_plan          TEXT,
    rollout_strategy   TEXT,
    doc_url            TEXT,
    impact_score       TEXT NOT NULL,   -- 'low' | 'medium' | 'high'
    effort_score       TEXT NOT NULL,   -- 'S' | 'M' | 'L' | 'XL'
    priority_score     NUMERIC(5,4),
    source_insight_ids UUID[] DEFAULT '{}',
    source_oss_ids     UUID[] DEFAULT '{}',
    status             rd_proposal_status NOT NULL DEFAULT 'draft',
    founder_decision_notes TEXT,
    founder_decided_at TIMESTAMPTZ,
    handoff_blueprint_id UUID REFERENCES blueprints(blueprint_id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_rd_proposals_status ON rd_proposals (status);
CREATE INDEX idx_rd_proposals_priority ON rd_proposals (priority_score DESC);
