-- ════════════════════════════════════════════════════════════════════
-- 0007 — RLS baseline
-- Service role (used by all agents) bypasses RLS; anon role is denied.
-- ════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    tbl TEXT;
    tables TEXT[] := ARRAY[
        'markets','accounts','contacts','events','scores','opportunities',
        'blueprints','build_tasks','deployments','revenue_events','lead_conversions',
        'agent_actions','attribution_reports','outbound_communications',
        'nurture_sequences','contact_activity_log','research_dossiers',
        'generated_drafts','founder_review_queue','agent_telemetry',
        'agent_performance_metrics','api_call_log','rd_insights',
        'oss_candidates','rd_proposals'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', tbl);
        -- Service role gets full access (agents use service role)
        EXECUTE format(
            'DROP POLICY IF EXISTS service_all ON %I; '
            'CREATE POLICY service_all ON %I FOR ALL TO service_role USING (true) WITH CHECK (true);',
            tbl, tbl
        );
    END LOOP;
END
$$;
