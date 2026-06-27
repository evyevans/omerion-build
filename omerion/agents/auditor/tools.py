"""Tools for AUDITOR — Constitutional Guardian (RSI Agent #5).

This module contains ALL deterministic logic that does not require an LLM:

 - scan_audit_log()         — reads audit_log from Supabase
 - check_hitl_approval()    — verifies a claimed HITL approval is genuine
 - execute_git_revert()     — shell-invokes git to revert a file change
 - execute_config_revert()  — reverts a config value to its last known good state
 - persist_verdict()        — writes a ConstitutionalVerdict to auditor_verdicts
 - persist_weekly_report()  — writes a WeeklyComplianceSummary to Supabase
 - post_discord_violation() — posts critical-violation alert to Discord
 - post_discord_suspicious()— posts suspicious-flag alert to Discord
 - post_discord_heartbeat() — posts clean-sweep heartbeat to Discord
 - build_records_block()    — formats AuditRecords for the LLM verify prompt
 - extract_verdicts_json()  — parses and validates LLM verdict JSON output

All tools raise on unrecoverable errors; graph nodes catch and record them.
Failures in notification tools (Discord) NEVER raise — they log and return False.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import httpx

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.http import PermanentHTTPError, TransientHTTPError, safe_request
from omerion_core.llm.embeddings import embed
from omerion_core.logging import get_logger
from omerion_core.settings import settings

from .prompts import (
    CRITICAL_VIOLATION_ALERT,
    HEALTHY_HEARTBEAT,
    SUSPICIOUS_FLAG_ALERT,
)
from .state import AuditRecord, ConstitutionalVerdict, RuleID, Severity, WeeklyComplianceSummary

log = get_logger("omerion.agents.auditor")

# ─── Constants ───────────────────────────────────────────────────────────────

_DISCORD_TIMEOUT = 5.0
_MAX_DISCORD_LEN = 1_900        # Discord 2 000-char cap with headroom
_REPO_ROOT = os.getcwd()        # Resolved once at import — the omerion/ working directory

# The seven constitutional rule IDs as a frozen set for O(1) membership checks.
_ALL_RULE_IDS: frozenset[str] = frozenset([
    "COST_CAP_INCREASE", "UNAUTHORIZED_API", "HITL_BYPASS",
    "CORE_LOGIC_MUTATION", "SECRET_EXPOSURE", "SCHEMA_DRIFT", "SELF_REVERT_LOOP",
])

# API endpoints always on the whitelist — do not require agents.yaml lookup.
_BUILTIN_WHITELIST_HOSTS: frozenset[str] = frozenset([
    "api.anthropic.com",
    "api.openai.com",
    "api.pinecone.io",
    "api.github.com",
    "discord.com",
    "hooks.slack.com",
    "www.googleapis.com",
    "oauth2.googleapis.com",
])

# Files/paths that constitute "core logic" — mutations here are Rule 4 violations.
_CORE_LOGIC_PATTERNS: tuple[str, ...] = (
    "omerion_core/",
    "/graph.py",
    "/state.py",
    "/tools.py",
)

# Patterns that indicate raw secret exposure in audit payloads.
_SECRET_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE),          # OpenAI
    re.compile(r"ant-[A-Za-z0-9\-]{30,}", re.IGNORECASE),       # Anthropic
    re.compile(r"pcsk_[A-Za-z0-9]{30,}", re.IGNORECASE),        # Pinecone
    re.compile(r"ghp_[A-Za-z0-9]{30,}", re.IGNORECASE),         # GitHub PAT
    re.compile(r"eyJ[A-Za-z0-9+/=]{40,}"),                       # JWT / Supabase service key
    re.compile(r"service_role", re.IGNORECASE),                  # Supabase role name in payload
)


# ─── Supabase — audit log scan ────────────────────────────────────────────────

def scan_audit_log(
    *,
    window_hours: int = 24,
    triggering_event_id: str | None = None,
) -> list[AuditRecord]:
    """Pull audit records from `audit_log` table.

    If `triggering_event_id` is provided, fetch ONLY the record(s) whose
    `triggering_event_id` matches — this is the event-triggered path where
    AUDITOR must evaluate a specific HEALER or TRAINER action immediately.

    For the nightly-cron path (`triggering_event_id=None`), fetch all records
    from the last `window_hours` hours that have not yet been audited.
    """
    try:
        if triggering_event_id:
            resp = (
                supabase.table("audit_log")
                .select("*")
                .eq("triggering_event_id", triggering_event_id)
                .eq("audited", False)
                .execute()
            )
        else:
            since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
            resp = (
                supabase.table("audit_log")
                .select("*")
                .gte("created_at", since)
                .eq("audited", False)
                .order("created_at", desc=False)
                .limit(500)      # Safety: never process > 500 records in one run
                .execute()
            )
        rows = resp.data or []
    except Exception as exc:
        log.error("auditor_scan_failed", error=str(exc))
        raise

    records: list[AuditRecord] = []
    for row in rows:
        try:
            records.append(AuditRecord(
                audit_id=UUID(row["audit_id"]),
                source_agent=row.get("source_agent", "unknown"),
                action_type=row.get("action_type", "unknown"),
                target_resource=row.get("target_resource", ""),
                diff_summary=(row.get("diff_summary") or "")[:2_000],
                raw_payload=row.get("raw_payload") or {},
                created_at=row.get("created_at", ""),
                hitl_review_id=UUID(row["hitl_review_id"]) if row.get("hitl_review_id") else None,
                reverted=bool(row.get("reverted", False)),
                requires_git_revert=bool(row.get("requires_git_revert", False)),
            ))
        except Exception as exc:
            log.warning("auditor_record_parse_failed", row_id=row.get("audit_id"), error=str(exc))

    log.info("auditor_scan_complete", records_loaded=len(records), window_hours=window_hours)
    return records


def mark_records_audited(audit_ids: list[UUID]) -> None:
    """Mark records as processed so the next AUDITOR run skips them."""
    if not audit_ids:
        return
    try:
        supabase.table("audit_log").update({"audited": True}).in_(
            "audit_id", [str(a) for a in audit_ids]
        ).execute()
    except Exception as exc:
        log.warning("auditor_mark_audited_failed", count=len(audit_ids), error=str(exc))


# ─── Pre-LLM deterministic rule checks ───────────────────────────────────────

def _check_cost_cap_increase(record: AuditRecord) -> list[RuleID]:
    """Rule 1: flag config patches that raise a cost-related field by > 10%."""
    violations: list[RuleID] = []
    payload = record.raw_payload
    for key in ("per_run_cost_cap_usd", "per_skill_daily_cost_cap_usd", "cost_per_run_usd"):
        old_val = payload.get(f"old_{key}")
        new_val = payload.get(f"new_{key}")
        if old_val is not None and new_val is not None:
            try:
                old_f, new_f = float(old_val), float(new_val)
                if old_f > 0 and (new_f - old_f) / old_f > 0.10:
                    violations.append("COST_CAP_INCREASE")
                    break
            except (ValueError, TypeError):
                pass
    return violations


def _check_unauthorized_api(record: AuditRecord) -> list[RuleID]:
    """Rule 2: detect API calls to non-whitelisted hosts."""
    violations: list[RuleID] = []
    # Check the diff_summary and raw_payload for any URL pattern
    search_text = record.diff_summary + json.dumps(record.raw_payload)
    url_pattern = re.compile(r"https?://([a-zA-Z0-9.\-]+)(?:/[^\s\"']*)?")
    for match in url_pattern.finditer(search_text):
        host = match.group(1).lower()
        if host in _BUILTIN_WHITELIST_HOSTS:
            continue
        # Check agents.yaml api_whitelist
        try:
            cfg = settings.agent("global")
            whitelist: list[str] = cfg.get("api_whitelist", [])
            if any(host == w.lower() or host.endswith("." + w.lower()) for w in whitelist):
                continue
        except Exception:
            pass
        log.info("auditor_unauthorized_api_found", host=host, audit_id=str(record.audit_id))
        violations.append("UNAUTHORIZED_API")
        break
    return violations


def _check_hitl_bypass(record: AuditRecord) -> list[RuleID]:
    """Rule 3: verify any claimed HITL approval is genuine and approved."""
    # Only applies to actions that modify self-improvement surfaces
    protected_extensions = (".skill.md", "prompts.py", "agents.yaml")
    if not any(record.target_resource.endswith(e) for e in protected_extensions):
        return []

    if record.hitl_review_id is None:
        log.warning("auditor_hitl_bypass_no_review_id", audit_id=str(record.audit_id))
        return ["HITL_BYPASS"]

    try:
        resp = (
            supabase.table("founder_review_queue")
            .select("decision, decided_at")
            .eq("review_id", str(record.hitl_review_id))
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return ["HITL_BYPASS"]
        row = rows[0]
        if row.get("decision") != "approved" or not row.get("decided_at"):
            return ["HITL_BYPASS"]
    except Exception as exc:
        log.warning("auditor_hitl_check_failed", audit_id=str(record.audit_id), error=str(exc))
        return ["HITL_BYPASS"]  # Fail closed on database error

    return []


def _check_core_logic_mutation(record: AuditRecord) -> list[RuleID]:
    """Rule 4: flag any modification to core Python execution files."""
    target = record.target_resource
    if any(pattern in target for pattern in _CORE_LOGIC_PATTERNS):
        return ["CORE_LOGIC_MUTATION"]
    return []


def _check_secret_exposure(record: AuditRecord) -> list[RuleID]:
    """Rule 5: scan diff_summary and raw_payload for raw secret patterns."""
    search_text = record.diff_summary + json.dumps(record.raw_payload)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(search_text):
            return ["SECRET_EXPOSURE"]
    return []


def _check_schema_drift(record: AuditRecord) -> list[RuleID]:
    """Rule 6: detect DDL statements in raw_payload or diff_summary."""
    ddl_keywords = re.compile(
        r"\b(CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|DROP\s+COLUMN|ADD\s+COLUMN"
        r"|CREATE\s+INDEX|DROP\s+INDEX|TRUNCATE\s+TABLE)\b",
        re.IGNORECASE,
    )
    search_text = record.diff_summary + json.dumps(record.raw_payload)
    if ddl_keywords.search(search_text):
        return ["SCHEMA_DRIFT"]
    return []


def _check_self_revert_loop(record: AuditRecord) -> list[RuleID]:
    """Rule 7: detect any attempt to target the AUDITOR agent itself."""
    if "agents/auditor" in record.target_resource or "auditor" == record.source_agent.lower():
        # source_agent == "auditor" means AUDITOR is acting on itself — allowed.
        # But action_type "revert" or "disable" targeting auditor is Rule 7.
        if record.action_type in ("revert", "disable", "pause", "delete"):
            return ["SELF_REVERT_LOOP"]
    if "agents/auditor" in record.target_resource:
        return ["SELF_REVERT_LOOP"]
    return []


def run_deterministic_checks(record: AuditRecord) -> list[RuleID]:
    """Run all seven deterministic rule checks against a single AuditRecord.

    Returns the union of rule violations. An empty list means no deterministic
    violation was found; the LLM verify step still runs to catch semantic issues.
    """
    violations: list[RuleID] = []
    violations += _check_cost_cap_increase(record)
    violations += _check_unauthorized_api(record)
    violations += _check_hitl_bypass(record)
    violations += _check_core_logic_mutation(record)
    violations += _check_secret_exposure(record)
    violations += _check_schema_drift(record)
    violations += _check_self_revert_loop(record)
    # Deduplicate while preserving order
    seen: set[str] = set()
    return [v for v in violations if not (v in seen or seen.add(v))]  # type: ignore[func-returns-value]


# ─── Revert execution ─────────────────────────────────────────────────────────

def execute_git_revert(target_resource: str, audit_id: UUID) -> tuple[bool, str]:
    """Attempt to revert a file to its last committed state via git checkout.

    This is the nuclear option for Rule 4 (CORE_LOGIC_MUTATION) and Rule 7
    (SELF_REVERT_LOOP) violations. It restores the file to HEAD~1 if the last
    commit was an agent-authored change.

    Returns (success: bool, message: str).
    NEVER raises — all errors are captured and returned in `message`.
    """
    try:
        # Step 1: validate the target is within the repo and not auditor itself
        safe_path = os.path.normpath(os.path.join(_REPO_ROOT, target_resource))
        if not safe_path.startswith(_REPO_ROOT):
            return False, f"Path traversal detected: {target_resource}"
        if "auditor" in safe_path:
            return False, "AUDITOR refuses to revert its own files (Rule 7 safeguard)"

        # Step 2: attempt git checkout HEAD to restore last committed state
        result = subprocess.run(
            ["git", "checkout", "HEAD", "--", target_resource],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.info("auditor_git_revert_succeeded", resource=target_resource, audit_id=str(audit_id))
            return True, f"git checkout HEAD -- {target_resource} succeeded"

        # Step 3: fallback — try HEAD~1 if HEAD itself was the bad commit
        result2 = subprocess.run(
            ["git", "checkout", "HEAD~1", "--", target_resource],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result2.returncode == 0:
            log.info("auditor_git_revert_head1_succeeded", resource=target_resource, audit_id=str(audit_id))
            return True, f"git checkout HEAD~1 -- {target_resource} succeeded (HEAD was the bad commit)"

        msg = f"git revert failed: {result.stderr.strip() or result2.stderr.strip()}"
        log.error("auditor_git_revert_failed", resource=target_resource, error=msg)
        return False, msg

    except subprocess.TimeoutExpired:
        return False, "git revert timed out (30s)"
    except Exception as exc:
        log.error("auditor_git_revert_exception", resource=target_resource, error=str(exc))
        return False, str(exc)


def execute_config_revert(record: AuditRecord) -> tuple[bool, str]:
    """Revert a config/agents.yaml change to its previous value.

    Uses the `old_value` field in `raw_payload` to restore the prior state.
    Returns (success, message).
    """
    try:
        payload = record.raw_payload
        config_key = payload.get("config_key")
        old_value = payload.get("old_value")
        if not config_key or old_value is None:
            return False, "raw_payload missing config_key or old_value — cannot revert config"

        # Re-read agents.yaml and patch the key back to old_value
        import yaml
        config_path = os.path.join(_REPO_ROOT, "config", "agents.yaml")
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        # Navigate the dot-path key (e.g. "r4_evaluation_telemetry.regression_thresholds.latency_p95_ms")
        parts = config_key.split(".")
        target = config
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = old_value

        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

        log.info("auditor_config_reverted", key=config_key, old_value=old_value, audit_id=str(record.audit_id))
        return True, f"config key '{config_key}' restored to {old_value!r}"

    except Exception as exc:
        log.error("auditor_config_revert_failed", audit_id=str(record.audit_id), error=str(exc))
        return False, str(exc)


def revert_record(record: AuditRecord, verdict: ConstitutionalVerdict) -> ConstitutionalVerdict:
    """Dispatch to the appropriate revert strategy based on `record.requires_git_revert`."""
    if record.reverted:
        # Already reverted by a prior run — do not double-revert
        verdict.revert_executed = True
        verdict.revert_error = None
        return verdict

    if record.requires_git_revert:
        success, msg = execute_git_revert(record.target_resource, record.audit_id)
    else:
        success, msg = execute_config_revert(record)

    verdict.revert_executed = success
    verdict.revert_error = None if success else msg

    if success:
        # Mark the audit record as reverted in Supabase
        try:
            supabase.table("audit_log").update({"reverted": True}).eq(
                "audit_id", str(record.audit_id)
            ).execute()
        except Exception as exc:
            log.warning("auditor_mark_reverted_failed", audit_id=str(record.audit_id), error=str(exc))
    else:
        log.error("auditor_revert_failed", audit_id=str(record.audit_id), error=msg)

    return verdict


# ─── Persistence ─────────────────────────────────────────────────────────────

def persist_verdict(verdict: ConstitutionalVerdict, run_id: str, source_agent: str = "unknown") -> None:
    """Write a ConstitutionalVerdict to the `auditor_verdicts` table.

    source_agent is denormalized here so the weekly report leaderboard can read
    agent names without joining back to audit_log (which may be archived/cleaned).
    """
    try:
        supabase.table("auditor_verdicts").upsert(
            {
                "audit_id": str(verdict.audit_id),
                "run_id": run_id,
                "source_agent": source_agent,
                "severity": verdict.severity,
                "rules_violated": verdict.rules_violated,
                "revert_executed": verdict.revert_executed,
                "revert_error": verdict.revert_error,
                "verdict_reasoning": verdict.verdict_reasoning,
            },
            on_conflict="audit_id",
        ).execute()
    except Exception as exc:
        log.warning("auditor_persist_verdict_failed", audit_id=str(verdict.audit_id), error=str(exc))


def persist_weekly_report(summary: WeeklyComplianceSummary) -> UUID | None:
    """Write the WeeklyComplianceSummary to `auditor_weekly_reports`. Returns the new row UUID."""
    try:
        resp = supabase.table("auditor_weekly_reports").insert(
            {
                "report_date": summary.report_date.isoformat(),
                "window_days": summary.window_days,
                "total_records_scanned": summary.total_records_scanned,
                "compliant_count": summary.compliant_count,
                "suspicious_count": summary.suspicious_count,
                "critical_violation_count": summary.critical_violation_count,
                "reverted_count": summary.reverted_count,
                "top_offending_agents": summary.top_offending_agents,
                "narrative_md": summary.narrative_md,
            }
        ).execute()
        if resp.data:
            return UUID(resp.data[0]["report_id"])
    except Exception as exc:
        log.warning("auditor_persist_weekly_report_failed", error=str(exc))
    return None


# ─── Discord notifications ────────────────────────────────────────────────────

def _post_discord(url: str, content: str) -> bool:
    """Post a plain-text Discord message. Never raises.

    Discord rate-limits webhooks at 5 req/sec per channel and returns 429 or
    transient 5xx during shard rebalancing. safe_request handles both via the
    "discord" token bucket + exponential backoff so a single 429 doesn't
    silently lose a critical-violation alert.
    """
    if not url:
        return False
    try:
        safe_request(
            "POST", url,
            service="discord",
            json={"content": content[:_MAX_DISCORD_LEN]},
            timeout=_DISCORD_TIMEOUT,
            attempts=3,
            expected_status=(200, 204),  # Discord webhook returns 204 No Content on success
        )
        return True
    except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
        log.warning("auditor_discord_post_failed", error=str(exc),
                    error_class=type(exc).__name__)
        return False


def _alerts_webhook() -> str:
    """Return the configured Discord alerts webhook URL."""
    return (
        getattr(settings, "discord_alerts_webhook_url", "")
        or os.getenv("DISCORD_MISSION_CONTROL_WEBHOOK_URL", "")
    )


def post_discord_violation(record: AuditRecord, verdict: ConstitutionalVerdict) -> bool:
    """Post a critical-violation alert to Discord #alerts / #mission-control."""
    revert_status = "✅ Reverted" if verdict.revert_executed else (
        f"❌ Revert FAILED: {verdict.revert_error}" if verdict.revert_error else "⏳ Revert pending"
    )
    content = CRITICAL_VIOLATION_ALERT.format(
        source_agent=record.source_agent,
        target_resource=record.target_resource,
        rules_list=", ".join(f"`{r}`" for r in verdict.rules_violated) or "(unknown)",
        action_type=record.action_type,
        audit_id=str(record.audit_id),
        reasoning=verdict.verdict_reasoning[:600],
        revert_status=revert_status,
    )
    return _post_discord(_alerts_webhook(), content)


def post_discord_suspicious(record: AuditRecord, verdict: ConstitutionalVerdict) -> bool:
    """Post a suspicious-flag notice to Discord."""
    content = SUSPICIOUS_FLAG_ALERT.format(
        source_agent=record.source_agent,
        target_resource=record.target_resource,
        action_type=record.action_type,
        audit_id=str(record.audit_id),
        reasoning=verdict.verdict_reasoning[:600],
    )
    return _post_discord(_alerts_webhook(), content)


def post_discord_heartbeat(total_records: int, window_hours: int) -> bool:
    """Post a clean-sweep heartbeat when no violations are found."""
    content = HEALTHY_HEARTBEAT.format(
        total_records=total_records,
        window_hours=window_hours,
    )
    return _post_discord(_alerts_webhook(), content)


def post_discord_weekly_report(summary: WeeklyComplianceSummary, narrative_md: str) -> bool:
    """Post a truncated weekly report to Discord."""
    status = (
        "🚨 ALERT" if summary.critical_violation_count > 0
        else "⚠️ CAUTION" if summary.suspicious_count > 0
        else "✅ HEALTHY"
    )
    content = (
        f"**📋 AUDITOR Weekly Constitutional Report — {summary.report_date}** {status}\n\n"
        + narrative_md[:1_500]
    )
    return _post_discord(_alerts_webhook(), content)


# ─── LLM output helpers ───────────────────────────────────────────────────────

def build_records_block(records: list[AuditRecord]) -> str:
    """Format AuditRecords for the VERIFY_USER prompt."""
    if not records:
        return "(no records)"
    lines: list[str] = []
    for r in records:
        lines.append(
            f"---\naudit_id: {r.audit_id}\n"
            f"source_agent: {r.source_agent}\n"
            f"action_type: {r.action_type}\n"
            f"target_resource: {r.target_resource}\n"
            f"hitl_review_id: {r.hitl_review_id or 'null'}\n"
            f"diff_summary:\n{r.diff_summary[:800]}\n"
        )
    return "\n".join(lines)


_JSON_ARR_RE = re.compile(r"\[.*\]", re.DOTALL)
_VALID_SEVERITIES: frozenset[str] = frozenset(["compliant", "suspicious", "critical_violation"])


def extract_verdicts_json(
    raw_llm_output: str,
    expected_ids: list[UUID],
) -> list[ConstitutionalVerdict]:
    """Parse and validate the LLM's JSON verdict array.

    - Only accepts the seven known rule IDs.
    - Only accepts the three valid severity levels.
    - Any audit_id in `expected_ids` missing from the output is defaulted
      to `suspicious` (fail-safe: if the LLM dropped a record, flag it).
    """
    verdicts: list[ConstitutionalVerdict] = []
    raw_map: dict[str, dict[str, Any]] = {}

    m = _JSON_ARR_RE.search(raw_llm_output or "")
    if m:
        try:
            items = json.loads(m.group(0))
            for item in items:
                if not isinstance(item, dict):
                    continue
                aid = str(item.get("audit_id", ""))
                if not aid:
                    continue
                severity = str(item.get("severity", "suspicious")).lower()
                if severity not in _VALID_SEVERITIES:
                    severity = "suspicious"
                rules_raw = item.get("rules_violated") or []
                rules = [r for r in rules_raw if r in _ALL_RULE_IDS]
                raw_map[aid] = {
                    "severity": severity,
                    "rules_violated": rules,
                    "verdict_reasoning": str(item.get("verdict_reasoning", ""))[:1_000],
                }
        except json.JSONDecodeError as exc:
            log.warning("auditor_verdict_json_parse_failed", error=str(exc))

    # Build final verdict list; fill any missing IDs with suspicious defaults
    for exp_id in expected_ids:
        key = str(exp_id)
        data = raw_map.get(key, {
            "severity": "suspicious",
            "rules_violated": [],
            "verdict_reasoning": "AUDITOR LLM did not return a verdict for this record — defaulted to suspicious.",
        })
        verdicts.append(ConstitutionalVerdict(
            audit_id=exp_id,
            severity=data["severity"],  # type: ignore[arg-type]
            rules_violated=data["rules_violated"],
            verdict_reasoning=data["verdict_reasoning"],
        ))

    return verdicts


def load_violation_context(query_text: str, top_k: int = 3) -> list[str]:
    """Query infra_violations for past similar violation patterns.

    Returns up to top_k text snippets (≤150 tokens total) as advisory context
    for the LLM verify step. Failure is silently swallowed — the deterministic
    rule checks run regardless of whether this succeeds.

    CONSTRAINT: these snippets are advisory only. They cannot upgrade or downgrade
    a deterministic verdict. The LLM may use them to spot novel patterns that
    resemble past violations without triggering an exact string match.
    """
    if not getattr(settings, "pinecone_api_key", None):
        return []
    try:
        vector = embed(query_text)
        idx = pinecone_index()
        result = idx.query(
            vector=vector,
            top_k=top_k,
            include_metadata=True,
            filter={"agent_id": {"$eq": "auditor"}},
            namespace="infra_violations",
        )
        snippets: list[str] = []
        for match in result.matches:
            if match.score > 0.70 and match.metadata.get("text"):
                snippets.append(match.metadata["text"][:150])
        return snippets
    except Exception as exc:
        log.warning("auditor_load_violation_context_failed", error=str(exc))
        return []


def embed_violations(
    verdicts: list[ConstitutionalVerdict],
    record_map: dict[UUID, AuditRecord],
    run_id: str,
) -> int:
    """Embed critical_violation and suspicious verdicts into the infra_violations namespace.

    Called at the end of notify_persist_node — AFTER Supabase writes and Discord alerts.
    Embedding failure must never surface as a graph exception: the primary persistence
    path (Supabase) already completed, so we log and return 0.

    Fleet manifest metadata pattern (matches outreach_signals/growth_contacts standard):
    agent_id / department / namespace / run_date / content_date + domain fields.
    """
    if not getattr(settings, "pinecone_api_key", None):
        return 0

    flagged = [v for v in verdicts if v.severity in ("critical_violation", "suspicious")]
    if not flagged:
        return 0

    try:
        from omerion_core.clients.pinecone_client import pinecone_index
        from omerion_core.llm.embeddings import embed

        idx = pinecone_index()
        upserts = []
        today = date.today().isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()

        for v in flagged:
            record = record_map.get(v.audit_id)
            source = record.source_agent if record else "unknown"
            text = (
                f"{v.severity}: rules={', '.join(v.rules_violated)} "
                f"agent={source} — {v.verdict_reasoning[:400]}"
            )
            vector = embed(text)
            upserts.append({
                "id": f"violation:{v.audit_id}",
                "values": vector,
                "metadata": {
                    # Fleet manifest fields
                    "agent_id": "auditor",
                    "department": "infra",
                    "namespace": "infra_violations",
                    "run_date": today,
                    "content_date": now_iso,
                    # Domain fields
                    "audit_id": str(v.audit_id),
                    "severity": v.severity,
                    "rules_violated": json.dumps(v.rules_violated),
                    "source_agent": source,
                    "run_id": run_id,
                    "text": text[:900],
                },
            })

        if upserts:
            idx.upsert(vectors=upserts, namespace="infra_violations")
        log.info("auditor_violations_embedded", count=len(upserts), run_id=run_id)
        return len(upserts)

    except Exception as exc:
        log.warning("auditor_embed_violations_failed", error=str(exc))
        return 0


def build_violations_block(verdicts: list[ConstitutionalVerdict], records: list[AuditRecord]) -> str:
    """Format critical violation details for the weekly report prompt."""
    violations = [v for v in verdicts if v.severity == "critical_violation"]
    if not violations:
        return "(none)"
    record_map = {r.audit_id: r for r in records}
    lines: list[str] = []
    for v in violations:
        r = record_map.get(v.audit_id)
        lines.append(
            f"- audit_id={v.audit_id} | agent={r.source_agent if r else '?'} "
            f"| rules={v.rules_violated} | reverted={v.revert_executed} "
            f"| reason: {v.verdict_reasoning[:200]}"
        )
    return "\n".join(lines)


def build_suspicious_block(verdicts: list[ConstitutionalVerdict], records: list[AuditRecord]) -> str:
    """Format suspicious flag details for the weekly report prompt."""
    suspicious = [v for v in verdicts if v.severity == "suspicious"]
    if not suspicious:
        return "(none)"
    record_map = {r.audit_id: r for r in records}
    lines: list[str] = []
    for v in suspicious:
        r = record_map.get(v.audit_id)
        lines.append(
            f"- audit_id={v.audit_id} | agent={r.source_agent if r else '?'} "
            f"| reason: {v.verdict_reasoning[:200]}"
        )
    return "\n".join(lines)


# ─── Auto-pause ─────────────────────────────────────────────────────────────


def auto_pause_agent(agent_name: str, reason: str) -> bool:
    """Set agent_schedule_enabled=False in agent_config. Returns True if updated.

    Idempotent: if the agent is already paused, updates the reason and returns True.
    Skips if no agent_config row exists for this agent (avoids phantom pauses).
    """
    from omerion_core.clients.supabase_client import supabase

    _log = get_logger("omerion.auditor.auto_pause")

    try:
        existing = (
            supabase.table("agent_config")
            .select("agent_name, agent_schedule_enabled")
            .eq("agent_name", agent_name)
            .limit(1)
            .execute()
        )
        if not existing.data:
            _log.warning("auto_pause_no_config_row", agent=agent_name)
            return False

        supabase.table("agent_config").update({
            "agent_schedule_enabled": False,
            "paused_reason": reason[:500],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("agent_name", agent_name).execute()

        _log.warning("auditor_auto_paused_agent", agent=agent_name, reason=reason)
        return True
    except Exception as exc:
        _log.error("auto_pause_failed", agent=agent_name, error=str(exc))
        return False
