-- 0044_deployer_health_log.sql
-- Tracks the outcome of every DEPLOYER pipeline run.
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS deployer_health_log (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id       uuid NOT NULL REFERENCES deployments(deployment_id),
    backup_ref          text,
    migration_ok        boolean NOT NULL DEFAULT false,
    provision_ok        boolean NOT NULL DEFAULT false,
    smoke_ok            boolean NOT NULL DEFAULT false,
    rollback_attempted  boolean NOT NULL DEFAULT false,
    rollback_ok         boolean,
    failure_reason      text,
    outcome             text NOT NULL,   -- confirmed | health_failed | rollback_ok | rollback_failed
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS deployer_health_log_deployment_id_idx
    ON deployer_health_log(deployment_id);

NOTIFY pgrst, 'reload schema';
