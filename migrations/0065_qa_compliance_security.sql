-- migrations/0065_qa_compliance_security.sql
-- QA test results
CREATE TABLE IF NOT EXISTS qa_test_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL,
    build_task_id   UUID REFERENCES build_tasks(task_id) ON DELETE SET NULL,
    agent_name      TEXT NOT NULL DEFAULT 'qa_tester',
    status          TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'error')),
    tests_total     INT NOT NULL DEFAULT 0,
    tests_passed    INT NOT NULL DEFAULT 0,
    tests_failed    INT NOT NULL DEFAULT 0,
    coverage_pct    NUMERIC(5,2),
    failure_summary TEXT,
    raw_output      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS qa_test_results_run_id_idx ON qa_test_results(run_id);
CREATE INDEX IF NOT EXISTS qa_test_results_status_idx ON qa_test_results(status);

-- Compliance violations
CREATE TABLE IF NOT EXISTS compliance_violations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL,
    rule_id         TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
    target_agent    TEXT,
    target_client   UUID,
    description     TEXT NOT NULL,
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS compliance_violations_run_id_idx ON compliance_violations(run_id);
CREATE INDEX IF NOT EXISTS compliance_violations_severity_idx ON compliance_violations(severity);

-- Security findings
CREATE TABLE IF NOT EXISTS security_findings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL,
    finding_type    TEXT NOT NULL CHECK (finding_type IN ('secret', 'dependency_cve', 'exposed_endpoint', 'config_drift')),
    severity        TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    resource        TEXT NOT NULL,
    description     TEXT NOT NULL,
    cve_id          TEXT,
    remediation     TEXT,
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS security_findings_run_id_idx ON security_findings(run_id);
CREATE INDEX IF NOT EXISTS security_findings_severity_idx ON security_findings(severity);
CREATE INDEX IF NOT EXISTS security_findings_resolved_idx ON security_findings(resolved);

NOTIFY pgrst, 'reload schema';
