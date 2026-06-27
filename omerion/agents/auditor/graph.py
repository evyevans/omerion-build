"""LangGraph for AUDITOR — Constitutional Guardian (RSI Agent #5).

Flow (nightly cron):
    scan_audit_log
      → run_deterministic_checks   (inline: fast, no LLM)
      → verify_with_llm            (Claude Sonnet — semantic rule evaluation)
      → revert_violations          (executes git/config reverts on critical violations)
      → notify_and_persist         (Discord alerts + Supabase writes)
      → generate_weekly_report     (Claude Sonnet — only on Monday, else skip)
      → emit                       (audit.sweep.complete OR audit.violation.detected)

Flow (event-triggered: healing.applied / prompt.update_proposed):
    Same graph. scan_audit_log filters to the triggering event's record only.
    Weekly report generation is skipped in event-triggered mode.

Design decisions:
  - deterministic_checks runs BEFORE the LLM to pre-classify obvious violations
    and give the LLM a warm signal. The LLM has final say only if deterministic
    checks return compliant (it can escalate, never downgrade a critical).
  - revert_violations executes IMMEDIATELY on critical verdicts — no HITL wait.
    The constitution's value is that it acts without permission. The founder
    is notified after the revert, not before.
  - suspicious verdicts go through HITL for founder confirmation before any action.
  - Weekly report uses HITL so the founder explicitly sees and acknowledges it.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .prompts import (
    VERIFY_SYSTEM,
    VERIFY_USER,
    WEEKLY_REPORT_SYSTEM,
    WEEKLY_REPORT_USER,
)
from .state import (
    AuditRecord,
    AuditorState,
    ConstitutionalVerdict,
    WeeklyComplianceSummary,
)
from .tools import (
    build_records_block,
    build_suspicious_block,
    build_violations_block,
    embed_violations,
    extract_verdicts_json,
    load_violation_context,
    mark_records_audited,
    persist_verdict,
    persist_weekly_report,
    post_discord_heartbeat,
    post_discord_violation,
    post_discord_weekly_report,
    revert_record,
    run_deterministic_checks,
    scan_audit_log,
)

log = get_logger("omerion.agents.auditor")

# ─── Nodes ────────────────────────────────────────────────────────────────────


@traced_node("load_violation_context")
def load_violation_context_node(state: AuditorState) -> AuditorState:
    """Node 1b — Query infra_violations for past similar patterns (advisory).

    Runs AFTER scan but BEFORE verify. Provides the LLM with semantic context
    about past violations so it can spot novel patterns that match the spirit
    of a rule without triggering its exact string check.

    Failure is non-fatal: deterministic checks run regardless.
    Constraint: these snippets cannot override a deterministic verdict.
    """
    if not state.audit_records:
        return state
    # Build a summary query from the batch's action types and resources
    action_types = list({r.action_type for r in state.audit_records})
    resources = list({r.target_resource for r in state.audit_records})
    query = f"constitutional violation {' '.join(action_types)} {' '.join(resources[:3])}"
    state.violation_context = load_violation_context(query[:500])
    log.info(
        "auditor_violation_context_loaded",
        snippets=len(state.violation_context),
    )
    return state


@traced_node("scan_audit_log")
def scan_node(state: AuditorState) -> AuditorState:
    """Node 1 — Load audit records from Supabase.

    For nightly_cron: fetches all unaudited records from the past scan_window_hours.
    For event-triggered: fetches only the record(s) linked to triggering_event_id.
    """
    records = scan_audit_log(
        window_hours=state.scan_window_hours,
        triggering_event_id=state.triggering_event_id,
    )
    state.audit_records = records
    state.records_scanned = len(records)
    log.info("auditor_scan_complete", count=len(records), mode=state.trigger_mode)
    return state


@traced_node("verify_guardrails")
def verify_node(state: AuditorState) -> AuditorState:
    """Node 2 — Constitutional verification.

    Step 2a: Run seven deterministic rule checks on every record (O(n), fast).
    Step 2b: Send ALL records to Claude Sonnet for semantic evaluation.
             The LLM operates on the deterministic pre-classification as context
             so it can focus on edge cases rather than obvious violations.
    Step 2c: Merge deterministic + LLM verdicts. Rule: if deterministic says
             critical_violation, the final verdict is critical_violation regardless
             of what the LLM returns (LLM can never downgrade a deterministic hit).
    """
    if not state.audit_records:
        log.info("auditor_no_records_to_verify")
        return state

    # ── Step 2a: deterministic pre-classification ──────────────────────────
    deterministic_violations: dict[UUID, list] = {}
    for record in state.audit_records:
        violations = run_deterministic_checks(record)
        if violations:
            deterministic_violations[record.audit_id] = violations
            log.info(
                "auditor_deterministic_violation",
                audit_id=str(record.audit_id),
                rules=violations,
            )

    # ── Step 2b: LLM semantic evaluation ──────────────────────────────────
    records_block = build_records_block(state.audit_records)
    violation_context_block = (
        "\n".join(f"- {s}" for s in state.violation_context)
        if state.violation_context
        else "(no prior violations on record — first sweep or namespace empty)"
    )
    router = ClaudeRouter()
    resp = router.complete(
        system=VERIFY_SYSTEM,
        prompt=VERIFY_USER.format(
            record_count=len(state.audit_records),
            scan_window_hours=state.scan_window_hours,
            run_date=date.today().isoformat(),
            violation_context=violation_context_block,
            records_block=records_block,
        ),
        tier=Tier.DEFAULT,          # Sonnet — sufficient for structured verdict generation
        max_tokens=4_096,
        temperature=0.0,            # Zero temperature — deterministic security decisions
    )
    state.record_llm(resp["usage"], resp["cost_usd"])

    llm_verdicts = extract_verdicts_json(
        resp["text"],
        expected_ids=[r.audit_id for r in state.audit_records],
    )

    # ── Step 2c: merge — deterministic always wins on violations ──────────
    final_verdicts: list[ConstitutionalVerdict] = []
    for v in llm_verdicts:
        determ_rules = deterministic_violations.get(v.audit_id, [])
        if determ_rules:
            # Deterministic hit: escalate to critical_violation, merge rule lists
            merged_rules = list(dict.fromkeys(determ_rules + v.rules_violated))
            v.severity = "critical_violation"
            v.rules_violated = merged_rules
            if "DETERMINISTIC CHECK" not in v.verdict_reasoning:
                v.verdict_reasoning = (
                    f"[DETERMINISTIC CHECK] Rules {determ_rules} triggered before LLM. "
                    + v.verdict_reasoning
                )
        final_verdicts.append(v)

    state.verdicts = final_verdicts
    state.critical_violations = [v for v in final_verdicts if v.severity == "critical_violation"]
    state.suspicious_flags = [v for v in final_verdicts if v.severity == "suspicious"]

    log.info(
        "auditor_verify_complete",
        total=len(final_verdicts),
        critical=len(state.critical_violations),
        suspicious=len(state.suspicious_flags),
    )
    return state


@traced_node("revert_violations")
def revert_node(state: AuditorState) -> AuditorState:
    """Node 3 — Execute immediate reverts on critical violations.

    Critical violations are reverted WITHOUT waiting for founder approval.
    This is the constitutional contract: AUDITOR acts, then reports.
    Suspicious flags are NOT reverted here — they wait for HITL.
    """
    if not state.critical_violations:
        log.info("auditor_no_critical_violations_to_revert")
        return state

    record_map = {r.audit_id: r for r in state.audit_records}

    updated_verdicts: dict[UUID, ConstitutionalVerdict] = {v.audit_id: v for v in state.verdicts}

    for violation in state.critical_violations:
        record = record_map.get(violation.audit_id)
        if not record:
            log.warning("auditor_revert_no_record", audit_id=str(violation.audit_id))
            continue

        state.reverts_attempted += 1
        updated_verdict = revert_record(record, violation)
        updated_verdicts[violation.audit_id] = updated_verdict

        if updated_verdict.revert_executed:
            state.reverts_succeeded += 1
        else:
            state.reverts_failed += 1
            log.error(
                "auditor_revert_failed_critical",
                audit_id=str(violation.audit_id),
                error=updated_verdict.revert_error,
            )

    # Rebuild full verdicts list with updated revert statuses
    state.verdicts = list(updated_verdicts.values())
    state.critical_violations = [v for v in state.verdicts if v.severity == "critical_violation"]
    return state


@traced_node("notify_and_persist")
def notify_persist_node(state: AuditorState) -> AuditorState:
    """Node 4 — Persist all verdicts to Supabase; post Discord alerts.

    Sends:
    - One critical-violation alert per critical verdict (immediately actionable)
    - One suspicious-flag HITL review per suspicious verdict
    - A clean-sweep heartbeat if zero violations

    Marks all scanned records as `audited = true` in the audit_log table.
    """
    run_id = str(state.run_id)
    record_map = {r.audit_id: r for r in state.audit_records}

    for verdict in state.verdicts:
        record = record_map.get(verdict.audit_id)
        persist_verdict(verdict, run_id, source_agent=record.source_agent if record else "unknown")

        if verdict.severity == "critical_violation" and record:
            post_discord_violation(record, verdict)

        elif verdict.severity == "suspicious" and record:
            # Create a HITL review for the founder to confirm whether action is needed
            context = (
                f"**Suspicious Activity Flagged**\n\n"
                f"- Agent: `{record.source_agent}`\n"
                f"- Action: `{record.action_type}`\n"
                f"- Resource: `{record.target_resource}`\n"
                f"- Audit ID: `{record.audit_id}`\n\n"
                f"**AUDITOR Reasoning:**\n{verdict.verdict_reasoning}\n\n"
                f"**Diff summary:**\n```\n{record.diff_summary[:600]}\n```"
            )
            try:
                review = create_founder_review_task(
                    agent_name=state.agent_name,
                    session_id=state.session_id,
                    subject=f"AUDITOR — Suspicious: {record.source_agent} → {record.target_resource}",
                    context_md=context,
                    draft_ref={"kind": "audit_verdict", "audit_id": str(verdict.audit_id)},
                    correlation_id=state.correlation_id,
                )
                state.suspicious_review_ids.append(UUID(review["review_id"]))
            except Exception as exc:
                log.warning("auditor_hitl_create_failed", audit_id=str(verdict.audit_id), error=str(exc))

    # Clean-sweep heartbeat only when truly nothing was flagged
    if not state.critical_violations and not state.suspicious_flags:
        post_discord_heartbeat(state.records_scanned, state.scan_window_hours)

    # Mark all scanned records as audited
    mark_records_audited([r.audit_id for r in state.audit_records])

    # Embed confirmed violations into Pinecone infra_violations namespace.
    # Runs AFTER all Supabase writes + Discord alerts — failure is non-fatal.
    embed_violations(state.verdicts, record_map, run_id)

    # Auto-pause agents with repeated violations in this scan window.
    from collections import Counter
    from agents.auditor.tools import auto_pause_agent
    from omerion_core.settings import settings as _settings

    breach_threshold = getattr(_settings, "auditor_breach_threshold", 3)
    if state.verdicts:
        agent_violation_counts = Counter(
            record_map.get(v.audit_id, None) and record_map[v.audit_id].source_agent or ""
            for v in state.verdicts
            if v.severity == "critical_violation"
        )
        for bad_agent, count in agent_violation_counts.items():
            if count >= breach_threshold and bad_agent:
                paused = auto_pause_agent(
                    bad_agent,
                    reason=f"auditor: {count} critical violations in {state.scan_window_hours}h scan window",
                )
                if paused:
                    log.warning("auditor_auto_paused", agent=bad_agent, violation_count=count)

    log.info(
        "auditor_notify_persist_complete",
        verdicts_persisted=len(state.verdicts),
        hitl_reviews_created=len(state.suspicious_review_ids),
    )
    return state


@traced_node("generate_weekly_report")
def weekly_report_node(state: AuditorState) -> AuditorState:
    """Node 5 — Generate and deliver the weekly compliance report.

    This node runs only on the configured weekly_report_day (default: Monday)
    and ONLY in nightly_cron mode. Event-triggered runs skip it.

    The report is written by Claude Sonnet over a 7-day window of all verdicts
    (not just today's) so the founder gets a comprehensive view.
    """
    today = date.today()
    is_report_day = today.weekday() == state.weekly_report_day  # 0 = Monday
    if state.trigger_mode != "nightly_cron" or not is_report_day:
        log.info("auditor_weekly_report_skipped", trigger_mode=state.trigger_mode, is_report_day=is_report_day)
        return state

    # Fetch the past 7 days of verdicts for the comprehensive report.
    # (Bug fix: this used an undefined `supabase` name — it always NameError'd, so
    # the report was generated over ZERO rows. Use the imported client.)
    from omerion_core.clients.supabase_client import supabase as _supabase
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        resp = (
            _supabase.table("auditor_verdicts")
            .select("*")
            .gte("created_at", since)
            .execute()
        )
        week_rows = resp.data or []
    except Exception as exc:
        log.warning("auditor_weekly_fetch_failed", error=str(exc))
        week_rows = []

    total = len(week_rows)
    compliant = sum(1 for r in week_rows if r.get("severity") == "compliant")
    suspicious = sum(1 for r in week_rows if r.get("severity") == "suspicious")
    critical = sum(1 for r in week_rows if r.get("severity") == "critical_violation")
    reverted = sum(1 for r in week_rows if r.get("revert_executed"))
    reverts_ok = sum(1 for r in week_rows if r.get("revert_executed") and not r.get("revert_error"))
    reverts_fail = reverted - reverts_ok

    # Build offending-agent leaderboard from denormalized source_agent column.
    agent_counts: Counter = Counter()
    for r in week_rows:
        if r.get("severity") in ("suspicious", "critical_violation"):
            agent_counts[r.get("source_agent") or "unknown"] += 1
    top_agents = [a for a, _ in agent_counts.most_common(3)]

    violations_block = "\n".join(
        f"- audit_id={r['audit_id']} rules={r.get('rules_violated')} reason={str(r.get('verdict_reasoning',''))[:200]}"
        for r in week_rows if r.get("severity") == "critical_violation"
    ) or "(none)"
    suspicious_block_txt = "\n".join(
        f"- audit_id={r['audit_id']} reason={str(r.get('verdict_reasoning',''))[:200]}"
        for r in week_rows if r.get("severity") == "suspicious"
    ) or "(none)"

    router = ClaudeRouter()
    resp = router.complete(
        system=WEEKLY_REPORT_SYSTEM,
        prompt=WEEKLY_REPORT_USER.format(
            report_date=today.isoformat(),
            window_days=7,
            total_scanned=total,
            compliant_count=compliant,
            suspicious_count=suspicious,
            critical_count=critical,
            reverted_count=reverted,
            reverts_succeeded=reverts_ok,
            reverts_failed=reverts_fail,
            violations_block=violations_block,
            suspicious_block=suspicious_block_txt,
        ),
        tier=Tier.DEFAULT,
        max_tokens=2_048,
        temperature=0.2,
    )
    state.record_llm(resp["usage"], resp["cost_usd"])
    narrative = resp["text"].strip()

    summary = WeeklyComplianceSummary(
        report_date=today,
        window_days=7,
        total_records_scanned=total,
        compliant_count=compliant,
        suspicious_count=suspicious,
        critical_violation_count=critical,
        reverted_count=reverted,
        top_offending_agents=top_agents,
        narrative_md=narrative,
    )
    state.weekly_report = summary

    report_id = persist_weekly_report(summary)
    state.weekly_report_id = report_id

    # Post truncated report to Discord
    post_discord_weekly_report(summary, narrative)

    # HITL — founder sees and acknowledges the weekly report
    try:
        review = create_founder_review_task(
            agent_name=state.agent_name,
            session_id=state.session_id,
            subject=f"AUDITOR Weekly Compliance Report — {today.isoformat()}",
            context_md=narrative[:3_000],
            draft_ref={"kind": "auditor_weekly_report", "report_id": str(report_id)},
            correlation_id=state.correlation_id,
        )
        log.info("auditor_weekly_hitl_created", review_id=review["review_id"])
    except Exception as exc:
        log.warning("auditor_weekly_hitl_failed", error=str(exc))

    log.info(
        "auditor_weekly_report_complete",
        total=total,
        critical=critical,
        suspicious=suspicious,
    )
    return state


@traced_node("emit")
def emit_node(state: AuditorState) -> AuditorState:
    """Node 6 — Emit the terminal event for downstream consumers.

    Emits `audit.violation.detected` if critical violations were found,
    otherwise `audit.sweep.complete` (heartbeat).
    """
    if state.critical_violations:
        # Dedicated audit event (NOT REGRESSION_ALERT — that wakes the HEALER).
        # No auto-consumer: the founder is notified via Discord + the dashboard.
        emit_event(
            EventType.AUDIT_VIOLATION_DETECTED,
            source_agent=state.agent_name,
            payload={
                "trigger": "auditor_critical_violation",
                "critical_count": len(state.critical_violations),
                "suspicious_count": len(state.suspicious_flags),
                "reverts_succeeded": state.reverts_succeeded,
                "reverts_failed": state.reverts_failed,
                "run_id": str(state.run_id),
            },
            correlation_id=state.correlation_id,
        )
        log.info("auditor_emit_violation_event", critical=len(state.critical_violations))
    else:
        emit_event(
            EventType.AUDIT_SWEEP_COMPLETE,
            source_agent=state.agent_name,
            payload={
                "status": "auditor_clean_sweep",
                "records_scanned": state.records_scanned,
                "run_id": str(state.run_id),
            },
            correlation_id=state.correlation_id,
        )
        log.info("auditor_emit_clean_sweep", scanned=state.records_scanned)

    return state


# ─── Graph assembly ───────────────────────────────────────────────────────────


def build():
    """Compile and return the AUDITOR LangGraph.

    Uses PostgresSaver checkpointer for HITL interrupt support on the
    suspicious-flag path (though AUDITOR's critical path does not wait for
    human approval — that is intentional by constitutional design).
    """
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(AuditorState)

    g.add_node("scan_audit_log", scan_node)
    g.add_node("load_violation_context", load_violation_context_node)
    g.add_node("verify_guardrails", verify_node)
    g.add_node("revert_violations", revert_node)
    g.add_node("notify_and_persist", notify_persist_node)
    g.add_node("generate_weekly_report", weekly_report_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("scan_audit_log")
    g.add_edge("scan_audit_log", "load_violation_context")
    g.add_edge("load_violation_context", "verify_guardrails")
    g.add_edge("verify_guardrails", "revert_violations")
    g.add_edge("revert_violations", "notify_and_persist")
    g.add_edge("notify_and_persist", "generate_weekly_report")
    g.add_edge("generate_weekly_report", "emit")
    g.add_edge("emit", END)

    return g.compile(checkpointer=get_checkpointer())
