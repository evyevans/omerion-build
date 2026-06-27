-- 0033_agent_config.sql
-- Per-agent runtime configuration: enables r4 auto-pause on critical regression.
--
-- One row per agent. `agent_schedule_enabled = false` is the kill switch checked
-- by every scheduled trigger (APScheduler cron, event-bus broker) before
-- dispatching a run. r4_evaluation_telemetry sets it to false when an agent
-- breaches a critical threshold; re-enabling requires explicit founder action.

CREATE TABLE IF NOT EXISTS agent_config (
    agent_name TEXT PRIMARY KEY,
    agent_schedule_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    persona_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    paused_at TIMESTAMPTZ,
    paused_reason TEXT,
    paused_by TEXT,
    client_id UUID REFERENCES clients(client_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_config_enabled
    ON agent_config (agent_schedule_enabled)
    WHERE agent_schedule_enabled = FALSE;

CREATE INDEX IF NOT EXISTS idx_agent_config_client
    ON agent_config (client_id)
    WHERE client_id IS NOT NULL;

-- Seed one row per known agent. Idempotent: ON CONFLICT does nothing.
INSERT INTO agent_config (agent_name) VALUES
    ('biz_dev_outreach'),
    ('build_orchestrator'),
    ('client_onboarding'),
    ('client_success'),
    ('competitive_intel'),
    ('crm_nurture'),
    ('high_quality_lead_scraping'),
    ('icp_scoring'),
    ('lead_scraper_enricher'),
    ('linkedin_outreach'),
    ('market_mapper'),
    ('meeting_intelligence'),
    ('offer_matching'),
    ('outcome_attribution'),
    ('r1_market_tech_watcher'),
    ('r2_oss_scout'),
    ('r3_strategic_architect'),
    ('r4_evaluation_telemetry')
ON CONFLICT (agent_name) DO NOTHING;
