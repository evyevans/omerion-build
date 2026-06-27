"""EventType → natural-language narration lines for #omerion-room.

Returns None for low-signal events that would create noise without value.
Every line uses the agent CODENAME (all-caps) so Evykynn knows who's talking.
"""
from __future__ import annotations

from typing import Callable

# Codename map: event source_agent values → display name
_AGENT_NAMES: dict[str, str] = {
    "market-mapper":           "MAPPER",
    "lead-scraper-enricher":   "SCOUT",
    "icp-scoring":             "SCORE",
    "linkedin-outreach":       "REACH",
    "crm-nurture":             "NURTURE",
    "offer-matching":          "MATCH",
    "meeting-intelligence":    "INTEL",
    "hq-lead-scraping":        "LEADS",
    "build-orchestrator":      "BUILD",
    "outcome-attribution":     "ATTR",
    "r1-market-tech-watcher":  "R1",
    "r2-oss-scout":            "R2",
    "r3-strategic-architect":  "R3",
    "r4-evaluation-telemetry": "R4",
    "job-seeker":              "SEEK",
    "system":                  "SYSTEM",
}


def _agent(p: dict) -> str:
    raw = p.get("source_agent", p.get("agent", ""))
    # state.agent_name uses underscore format; _AGENT_NAMES keys use hyphen format
    normalized = raw.replace("_", "-") if raw else raw
    return _AGENT_NAMES.get(normalized, raw.upper() if raw else "SYSTEM")


def _n(p: dict, key: str, default: int = 1) -> int:
    try:
        return int(p.get(key, default))
    except (TypeError, ValueError):
        return default


# Map: event_type string → formatter callable
_NARRATION: dict[str, Callable[[dict], str | None]] = {
    "account.discovered": lambda p: (
        f"🗺️ **MAPPER** found {_n(p,'count')} new account(s)"
        + (f" in {p['market']}" if p.get("market") else "")
        + " — passing to SCOUT"
    ),
    "account.batch.ready": lambda p: (
        f"📦 **MAPPER** batch ready ({_n(p,'count',0)} accounts) → SCORE queued"
    ),
    "contact.enriched": lambda p: (
        f"🔍 **SCOUT** enriched {_n(p,'count')} contact(s)"
    ),
    "contact.scored": lambda p: (
        f"📊 **SCORE** rated {_n(p,'count')} contact(s) — "
        f"{_n(p,'hot',0)} 🔥 hot · {_n(p,'warm',0)} warm · {_n(p,'watchlist',0)} watchlist"
    ),
    "contact.cohort.ready": lambda p: (
        f"🎯 **SCORE** cohort ready: {_n(p,'hot',0)} hot leads queued for REACH + NURTURE"
    ),
    "outreach.linkedin.sent": lambda p: (
        f"📤 **REACH** sent LinkedIn message to {p.get('name','a contact')}"
    ),
    "outreach.email.sent": lambda p: (
        f"📧 **NURTURE** sent email to {p.get('name','a contact')}"
    ),
    "outreach.replied": lambda p: (
        f"💬 **{_agent(p)}** — {p.get('name','A contact')} replied → handing to NURTURE"
    ),
    "outreach.ghosted": lambda p: (
        f"👻 **{_agent(p)}** — {p.get('name','contact')} ghosted after {_n(p,'days',0)}d"
    ),
    "outreach.reengaged": lambda p: (
        f"🔄 **NURTURE** — {p.get('name','contact')} re-engaged!"
    ),
    "outreach.thread.created": lambda p: None,  # too low-signal
    "outreach.signal.indexed": lambda p: None,   # too low-signal
    "outreach.sms.sent": lambda p: (
        f"📱 **NURTURE** sent SMS to {p.get('name','a contact')}"
    ),
    "dossier.created": lambda p: (
        f"📋 **LEADS** built dossier for {p.get('account','an account')}"
    ),
    "meeting.transcript.received": lambda p: (
        f"🎙️ **INTEL** received transcript"
        + (f" — {p.get('duration_min','')}min call" if p.get("duration_min") else "")
        + " · analysing now"
    ),
    "blueprint.draft.created": lambda p: (
        f"📝 **INTEL** drafted blueprint"
        + (f" for {p.get('persona','')}" if p.get("persona") else "")
        + " → awaiting your approval in #founder-hitl"
    ),
    "blueprint.approved": lambda p: (
        f"✅ Blueprint approved → **BUILD** is starting the project"
    ),
    "blueprint.rejected": lambda p: (
        f"❌ Blueprint rejected — INTEL will revise"
    ),
    "build.task.created": lambda p: (
        f"🔨 **BUILD** created task: {p.get('task_slug','new task')}"
    ),
    "build.task.completed": lambda p: (
        f"✅ **BUILD** completed task: {p.get('task_slug','task')}"
    ),
    "deployment.live": lambda p: (
        f"🚀 **BUILD** — {p.get('client_slug','client')} deployment is LIVE"
    ),
    "deployment.failed": lambda p: (
        f"🚨 **BUILD** — deployment failed for {p.get('client_slug','client')}: {p.get('error','unknown error')[:80]}"
    ),
    "attribution.report.ready": lambda p: (
        f"📈 **ATTR** report ready for {p.get('client_slug','client')}"
        + (f" — confidence: {p.get('confidence','')}" if p.get("confidence") else "")
    ),
    "rd.proposal.submitted": lambda p: (
        f"💡 **R3** submitted proposal: _{p.get('title','')}_"
        + " → pending your review"
    ),
    "rd.insights.batch.ready": lambda p: (
        f"📰 **R1** market digest ready ({_n(p,'count',0)} new insights)"
    ),
    "regression.alert": lambda p: (
        f"⚠️ **R4** flagged regression in `{p.get('agent','')}` — "
        f"{p.get('metric','')} ({p.get('value','')} > threshold {p.get('threshold','')})"
    ),
    "proposal.ready": lambda p: (
        f"📊 **R4** evaluation report ready"
    ),
    "hitl.approved": lambda p: None,   # shown by the bot's button interaction itself
    "hitl.rejected": lambda p: None,
    "founder.daily_digest": lambda p: None,  # bot posts digest directly
    "job.posting.discovered": lambda p: (
        f"🔎 **SEEK** found {_n(p,'count')} new job posting(s)"
        + (f" on {p.get('platform','')}" if p.get("platform") else "")
    ),
    "job.application.drafted": lambda p: (
        f"✍️ **SEEK** drafted application for **{p.get('company','')}** ({p.get('role','role')}) "
        f"→ awaiting your approval in #founder-hitl"
    ),
    "job.application.sent": lambda p: (
        f"📨 **SEEK** submitted application to {p.get('company','')} for {p.get('role','role')}"
    ),
    "job.application.responded": lambda p: (
        f"🎉 **SEEK** — {p.get('company','')} responded to your application!"
    ),
    "job.application.ghosted": lambda p: (
        f"👻 **SEEK** — {p.get('company','')} application ghosted after {_n(p,'days',14)}d"
    ),
    "account.updated": lambda p: None,  # too frequent / low-signal
}


def format_event(event_type: str, payload: dict) -> str | None:
    """Return a narration line for #omerion-room, or None to suppress."""
    formatter = _NARRATION.get(event_type)
    if formatter is None:
        return None
    try:
        return formatter(payload)
    except Exception:
        return None
