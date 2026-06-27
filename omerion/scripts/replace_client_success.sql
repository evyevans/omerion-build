-- Deterministic replacement for the retired `client_success` agent.
--
-- The original agent was a STUB (see omerion/agents/client_success/tools.py
-- `compute_health()` comment: "STUB scoring: counts agent_runs ... bands by
-- activity"). An LLM was being used to "compose check-in drafts" over the
-- same SQL math — which is exactly the "deterministic in LLM costume"
-- pattern the Wave 0 retirement targets.
--
-- This view does the same banding deterministically. Pair with an
-- APScheduler job that selects from the view daily at 18:00 America/Toronto
-- and posts the rollup to Discord #omerion-room.
--
-- Apply with:
--     psql -f omerion/scripts/replace_client_success.sql $DATABASE_URL
-- or run via the standard migration pipeline.

CREATE OR REPLACE VIEW client_health_today AS
WITH activity_7d AS (
    SELECT
        c.client_id,
        c.name                                 AS client_name,
        c.status                               AS client_status,
        COUNT(r.run_id)                        AS runs_7d,
        COALESCE(SUM(r.cost_usd), 0)::numeric  AS cost_7d_usd,
        MAX(r.started_at)                      AS last_run_at,
        SUM(CASE WHEN r.success IS FALSE THEN 1 ELSE 0 END) AS failures_7d
    FROM clients c
    LEFT JOIN agent_runs r
           ON r.client_slug = c.slug
          AND r.started_at >= now() - interval '7 days'
    WHERE c.status <> 'churned'
    GROUP BY c.client_id, c.name, c.status
),
hitl_backlog AS (
    SELECT
        client_slug,
        COUNT(*) FILTER (WHERE decision = 'pending')                 AS hitl_pending,
        COUNT(*) FILTER (WHERE decision = 'pending'
                          AND expires_at < now())                    AS hitl_expired
    FROM founder_review_queue
    GROUP BY client_slug
),
banded AS (
    SELECT
        a.*,
        COALESCE(h.hitl_pending, 0)  AS hitl_pending,
        COALESCE(h.hitl_expired, 0)  AS hitl_expired,
        CASE
            WHEN a.runs_7d = 0                                    THEN 'red'
            WHEN a.failures_7d::float / NULLIF(a.runs_7d, 0) > 0.10 THEN 'red'
            WHEN COALESCE(h.hitl_expired, 0) > 0                  THEN 'red'
            WHEN a.runs_7d < 5                                    THEN 'yellow'
            WHEN COALESCE(h.hitl_pending, 0) > 3                  THEN 'yellow'
            ELSE 'green'
        END AS health_band
    FROM activity_7d a
    LEFT JOIN hitl_backlog h ON h.client_slug = a.client_id::text
)
SELECT
    client_id,
    client_name,
    client_status,
    health_band,
    runs_7d,
    failures_7d,
    cost_7d_usd,
    hitl_pending,
    hitl_expired,
    last_run_at
FROM banded
ORDER BY
    CASE health_band WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END,
    cost_7d_usd DESC;

-- Allow the service-role and authenticated dashboard reads.
GRANT SELECT ON client_health_today TO PUBLIC;

COMMENT ON VIEW client_health_today IS
'Replaces the retired client_success agent (Wave 0). Deterministic 7-day '
'health bands per client; no LLM. Read from APScheduler daily Discord post '
'and from the dashboard MetricsBar.';
