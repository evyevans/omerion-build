---
name: security-auditor
tier: A
agent_number: 34
graph: agents.security_auditor.graph:build
triggers:
  - cron
  - event:deployment.live
events_consumed:
  - deployment.live
events_emitted:
  - security.scan.passed
  - security.violation.detected
hitl: true
model_tier: DEFAULT
schedule: "30 0 * * *"
---

# SECURITY_AUDITOR — Proactive Security Scanning (Agent #34)

## Identity & Scope

SECURITY_AUDITOR runs three deterministic security scans nightly and after
every deployment. All finding detection is pure Python — the LLM is used only
on Mondays to synthesize an executive security brief for the founder.

## Scans Performed (all deterministic)

| Scan | Method | Severity on hit |
|---|---|---|
| SA-1: SECRET_DETECTION | Regex against known key patterns in .env files | critical |
| SA-2: DEPENDENCY_CVE | pip-audit subprocess + JSON parse | high/medium |
| SA-3: ENDPOINT_EXPOSURE | Grep for routes without auth dependency | medium |

LLM used ONLY on Mondays to write a 500-word executive security brief. All
detection logic is pure Python — the LLM never decides what is or isn't a finding.

## Graph (5 nodes)

1. `scan_secrets` — Regex scan of .env files for raw secret patterns
2. `scan_deps` — pip-audit subprocess → JSON parse → CVE findings
3. `scan_endpoints` — Grep for FastAPI routes missing auth dependency
4. `generate_brief` — LLM executive brief (ONLY on Monday, ONLY if findings exist)
5. `emit_and_alert` — Persist to security_findings, HITL on critical, emit event

## Escalation Policy

- **critical findings** (raw secrets, critical CVEs): Immediate HITL card to founder
- **Weekly brief** (Monday): HITL review card with prioritized executive summary
- **clean scan**: Emits `security.scan.passed` with no HITL

## W.A.R.T.T. Contract

- **W**: Nightly at 00:30 Toronto + on deployment.live
- **A**: LLM in 1 of 5 nodes (generate_brief) — Mondays + only if findings exist
- **R**: Reads from repo filesystem (secrets, deps, routes); writes to security_findings
- **T**: Cron `30 0 * * *` + event trigger on deployment.live
- **T**: pip-audit (subprocess tool, not MCP) — no external MCP dependencies
