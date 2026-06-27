"""Tools for TRAINER (Wave 5).

Five responsibilities:

  1. **Scope control** — `TRAINER_TARGET_AGENTS` whitelist. TRAINER
     analyzes only the 6 wrapper-migrated agents until more are
     migrated.
  2. **Performance window** — `fetch_performance_window` pulls last-7d
     KPIs from `agent_performance_metrics`, augmented with cost
     variance from `agent_telemetry`.
  3. **AST prompt reader** — `read_agent_prompts` parses the target
     agent's `prompts.py` file as text and extracts uppercase string
     constants. NEVER imports/execs the module. This is the single
     most important defensive primitive in TRAINER: an LLM proposal
     can't trick us into running malicious Python from the agent we're
     about to "improve" because we never run the file at all.
  4. **Deterministic guardrail** — `validate_proposal_text` rejects
     any LLM output that (a) contains code blocks, (b) contains a
     `class ... (BaseModel)` definition, or (c) changes the
     format-string placeholder set vs. the current text. These are
     the three failure modes a misbehaving LLM could use to alter an
     agent's schema or inject code.
  5. **Persistence** — `persist_proposal` upserts to
     `prompt_improvements` with idempotency_key = sha256 over
     (agent, constant, iso_week). DB UNIQUE handles dedupe; a TRAINER
     restart inside the same week is a silent no-op.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.util.idempotency import generate_key

from .prompts import GENERATE_IMPROVEMENT_USER, TRAINER_SYSTEM
from .state import AgentPerformance, PromptProposal, UnderperformingAgent

log = get_logger("omerion.agents.trainer")


# ─────────────────────────── scope ────────────────────────────────────

# Per user choice 2026-05-24: TRAINER analyzes only the 6 wrapper-migrated
# agents. The wrapper structures their telemetry (confidence floor, recipient
# verification, value bounds) so the "failure signal" we hand the LLM is
# richer. Expanding scope = add agent_name to this frozenset; nothing else.
#
# Path mapping: agent_name in agent_performance_metrics uses the directory
# slug (snake_case), e.g. 'crm_nurture'. The wrapper skill registry uses
# kebab-case ('crm-nurture'). We keep BOTH forms because the DB has historical
# data under various forms.
TRAINER_TARGET_AGENTS: frozenset[str] = frozenset({
    "linkedin_outreach",   "linkedin-outreach",
    "crm_nurture",         "crm-nurture",
    "offer_matching",      "offer-matching",
    "meeting_intelligence", "meeting-intel",
    "lead_scraper_enricher", "lead-scraper",
    "outcome_attribution", "outcome-attribution",
})

# Canonical (directory-slug) form — used to construct prompts.py paths.
_DIR_SLUG_BY_DB_NAME: dict[str, str] = {
    "linkedin_outreach": "linkedin_outreach",
    "linkedin-outreach": "linkedin_outreach",
    "crm_nurture": "crm_nurture",
    "crm-nurture": "crm_nurture",
    "offer_matching": "offer_matching",
    "offer-matching": "offer_matching",
    "meeting_intelligence": "meeting_intelligence",
    "meeting-intel": "meeting_intelligence",
    "lead_scraper_enricher": "lead_scraper_enricher",
    "lead-scraper": "lead_scraper_enricher",
    "outcome_attribution": "outcome_attribution",
    "outcome-attribution": "outcome_attribution",
}

_AGENTS_DIR = Path(__file__).resolve().parents[1]   # omerion/agents/


# ─────────────────────────── week + hash helpers ──────────────────────

def iso_week_key(d: date | None = None) -> str:
    """Return ISO week label like '2026-W21'."""
    d = d or date.today()
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─────────────────────────── Node 1: fetch_outcomes ───────────────────

def fetch_performance_window(days: int = 7) -> list[AgentPerformance]:
    """Aggregate per-agent KPIs over the last `days` days.

    Reads two Supabase tables:
      * `agent_performance_metrics` — daily pre-aggregates (R4-style)
      * `agent_telemetry` — per-node spans for cost variance

    Filtered to `TRAINER_TARGET_AGENTS` only. Returns one
    AgentPerformance per target agent that has at least one row in the
    window. Agents with zero activity are omitted — there's nothing for
    TRAINER to learn from a silent agent.
    """
    out: dict[str, AgentPerformance] = {}
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cutoff_date = (date.today() - timedelta(days=days)).isoformat()

    # 1. agent_performance_metrics (daily aggregates)
    try:
        rows = (
            supabase.table("agent_performance_metrics")
            .select(
                "agent_name,runs_total,runs_success,runs_failure,"
                "hitl_approvals,hitl_rejections,total_cost_usd,"
                "p95_duration_ms,avg_duration_ms,regression_flags"
            )
            .gte("metric_date", cutoff_date)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("trainer_metrics_query_failed", error=str(exc))
        rows = []

    for r in rows:
        name = r.get("agent_name", "")
        if name not in TRAINER_TARGET_AGENTS:
            continue
        ap = out.setdefault(name, AgentPerformance(agent_name=name))
        ap.runs_total += int(r.get("runs_total") or 0)
        ap.runs_failure += int(r.get("runs_failure") or 0)
        ap.hitl_approvals += int(r.get("hitl_approvals") or 0)
        ap.hitl_rejections += int(r.get("hitl_rejections") or 0)
        ap.total_cost_usd += float(r.get("total_cost_usd") or 0.0)
        ap.regression_flags += int(r.get("regression_flags") or 0)
        # Take the max p95_duration over the window — worst day is the signal
        ap.p95_duration_ms = max(
            ap.p95_duration_ms, float(r.get("p95_duration_ms") or 0.0)
        )

    # 2. agent_telemetry cost variance (median vs p95)
    try:
        tel = (
            supabase.table("agent_telemetry")
            .select("agent_name,cost_usd")
            .gte("created_at", cutoff_iso)
            .limit(5000)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("trainer_telemetry_query_failed", error=str(exc))
        tel = []

    by_agent: dict[str, list[float]] = {}
    for t in tel:
        name = t.get("agent_name", "")
        if name not in TRAINER_TARGET_AGENTS:
            continue
        cost = float(t.get("cost_usd") or 0.0)
        if cost > 0:
            by_agent.setdefault(name, []).append(cost)

    for name, costs in by_agent.items():
        ap = out.setdefault(name, AgentPerformance(agent_name=name))
        costs.sort()
        n = len(costs)
        median = costs[n // 2] if n else 0.0
        p95 = costs[max(int(0.95 * n) - 1, 0)] if n else 0.0
        ap.median_cost_usd = median
        ap.p95_cost_usd = p95
        ap.cost_variance_ratio = (p95 / median) if median > 0 else 0.0

    # Compute derived ratios.
    for ap in out.values():
        ap.failure_rate = ap.runs_failure / max(ap.runs_total, 1)
        denom = ap.hitl_approvals + ap.hitl_rejections
        ap.rejection_ratio = ap.hitl_rejections / max(denom, 1)

    return list(out.values())


# ─────────────────────────── Node 2: identify_underperformers ─────────

# Thresholds (Wave 5 plan §"identify underperforming prompts"). Tunable
# via settings later; hardcoded for the initial ship.
FAILURE_RATE_THRESHOLD       = 0.10   # >10% failures over the week
COST_VARIANCE_THRESHOLD      = 3.0    # p95_cost / median_cost > 3×
REJECTION_RATIO_THRESHOLD    = 0.30   # founder overrules >30%
REGRESSION_FLAGS_THRESHOLD   = 1      # any R4 flag this week


def identify_underperformers(metrics: list[AgentPerformance]) -> list[tuple[str, str]]:
    """Apply deterministic thresholds. Returns list of (agent_name, failure_signal).

    Each tuple's failure_signal is the human-readable summary the LLM
    consumes in Node 3 — it explicitly cites which thresholds breached
    so the rationale can ground itself in the same evidence.
    """
    out: list[tuple[str, str]] = []
    for m in metrics:
        reasons: list[str] = []
        if m.runs_total > 0 and m.failure_rate > FAILURE_RATE_THRESHOLD:
            reasons.append(
                f"failure_rate={m.failure_rate:.1%} "
                f"({m.runs_failure}/{m.runs_total} runs) exceeds "
                f"{FAILURE_RATE_THRESHOLD:.0%} threshold"
            )
        if m.cost_variance_ratio > COST_VARIANCE_THRESHOLD:
            reasons.append(
                f"cost variance p95/median = {m.cost_variance_ratio:.1f}× "
                f"(median ${m.median_cost_usd:.4f}, p95 ${m.p95_cost_usd:.4f}) — "
                f"a few runs are blowing up cost"
            )
        if m.rejection_ratio > REJECTION_RATIO_THRESHOLD:
            reasons.append(
                f"founder overruled {m.hitl_rejections} of "
                f"{m.hitl_approvals + m.hitl_rejections} HITL reviews "
                f"({m.rejection_ratio:.0%}) — agent's judgment is "
                f"out of step with founder"
            )
        if m.regression_flags >= REGRESSION_FLAGS_THRESHOLD:
            reasons.append(
                f"R4 raised {m.regression_flags} regression flag(s) this week"
            )
        if reasons:
            failure_signal = (
                f"Agent {m.agent_name} crossed thresholds:\n  - "
                + "\n  - ".join(reasons)
            )
            out.append((m.agent_name, failure_signal))
    return out


def read_agent_prompts(agent_name: str) -> dict[str, str]:
    """AST-parse `omerion/agents/<slug>/prompts.py` and return
    {ConstantName: literal_text} for every UPPERCASE str Assign.

    Critically: this function NEVER imports or execs the target module.
    The agent we're about to "improve" could contain hostile code in a
    module-level expression (think top-level side effects); we read the
    file as text and parse the AST, which is safe regardless of file
    contents.

    Returns an empty dict on any parse error — TRAINER skips an agent
    whose prompts can't be cleanly extracted rather than guessing.
    """
    slug = _DIR_SLUG_BY_DB_NAME.get(agent_name, agent_name.replace("-", "_"))
    path = _AGENTS_DIR / slug / "prompts.py"
    if not path.exists():
        log.warning("trainer_prompts_file_missing", agent=agent_name, path=str(path))
        return {}
    try:
        src = path.read_text()
        tree = ast.parse(src)
    except (OSError, SyntaxError) as exc:
        log.warning("trainer_prompts_parse_failed", agent=agent_name, error=str(exc))
        return {}

    out: dict[str, str] = {}
    for node in tree.body:
        # Plain string assigns: `NURTURE_SYSTEM = "..."`
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.isupper():
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    out[target.id] = node.value.value
        # f-strings (e.g., NURTURE_SYSTEM = f"...{UNIVERSAL_AGENT_RULES}...") —
        # we extract the format spec by AST-unparsing to preserve the prompt's
        # logical content. The rewritten proposal will be a plain string
        # though; TRAINER deliberately does not propose f-string templates.
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and target.id.isupper()
                and isinstance(node.value, ast.JoinedStr)
            ):
                try:
                    out[target.id] = ast.unparse(node.value)
                except Exception:  # noqa: BLE001
                    continue
    return out


# ─────────────────────────── Node 3: generate_improvements ────────────

# Regex guardrails — deterministic, no LLM. These run on every proposed_text
# before it's stored or shown to the founder. Compiled once at import.
_CODE_FENCE_RE  = re.compile(r"```")
_CLASS_DEF_RE   = re.compile(r"\bclass\s+\w+\s*\(.*BaseModel.*\)\s*:", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


def validate_proposal_text(current: str, proposed: str) -> tuple[bool, str]:
    """Return (ok, reason) for the proposed prompt text.

    Three deterministic checks (Wave 5 guardrail #1 in TWAT spec):
      1. No Markdown/code fences (``` markers banned).
      2. No `class X(BaseModel)` definitions (schema change banned).
      3. The set of format-string placeholders must be IDENTICAL.
         An LLM that renames `{persona}` → `{persona_name}` would break
         every call site of the prompt template; this check catches it.

    The placeholder check is the most consequential of the three — an
    LLM proposal that passes the first two but renames a placeholder
    would silently fail the next run of the target agent.
    """
    if _CODE_FENCE_RE.search(proposed):
        return False, "proposed_text contains code fences (``` banned)"
    if _CLASS_DEF_RE.search(proposed):
        return False, "proposed_text contains a class definition (schema change banned)"

    current_placeholders = set(_PLACEHOLDER_RE.findall(current))
    proposed_placeholders = set(_PLACEHOLDER_RE.findall(proposed))
    if current_placeholders != proposed_placeholders:
        diff = current_placeholders.symmetric_difference(proposed_placeholders)
        return False, f"format-string placeholder set changed: {sorted(diff)}"
    return True, ""


# ─── Wave 5 v2.5 helpers: build the evidence blocks ────────────────────


def extract_load_bearing_clauses(current_text: str, *, max_clauses: int = 8) -> list[str]:
    """Deterministically extract sentences that look like rules the agent's
    correctness depends on. These get passed to the LLM as
    "load_bearing_clauses_block" so it can verify each survived the rewrite.

    Heuristic (no LLM): a sentence is load-bearing if it contains any of:
      * "MUST" / "NEVER" / "MUST NEVER" / "ALWAYS"
      * "do not" / "don't"
      * "required" / "mandatory" / "non-negotiable"
      * "only" (e.g., "only output JSON")
      * "must be" / "shall"

    We deliberately use a syntactic heuristic, not an LLM, because:
      1. Deterministic → reproducible across TRAINER runs.
      2. No second LLM call cost.
      3. False positives are cheap (LLM just confirms the extra clause
         survived); false negatives are the dangerous case.
    """
    LOAD_BEARING_MARKERS = (
        " must ", " must.", " must,", "MUST ", "MUST,",
        " never ", "NEVER ", " always ", "ALWAYS ",
        " do not ", " don't ", "do NOT ", "Do not ",
        " required", " mandatory", "non-negotiable",
        " only ", "Only ",
        " shall ",
    )
    # Naive sentence split — good enough for prompt extraction.
    sentences = re.split(r"(?<=[.!?])\s+|(?<=:\n)", current_text)
    out: list[str] = []
    for s in sentences:
        s = s.strip()
        if len(s) < 15 or len(s) > 500:
            continue
        if any(m in s for m in LOAD_BEARING_MARKERS):
            out.append(s)
        if len(out) >= max_clauses:
            break
    return out


def fetch_past_approved_rewrites(
    target_agent_name: str | None = None,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Pull the N most-recent founder-approved TRAINER proposals for
    house-style few-shot examples. Filter by target_agent_name when
    provided so the LLM sees rewrites for similar-shape agents.
    """
    try:
        q = (
            supabase.table("prompt_improvements")
            .select("target_agent_name,prompt_constant_name,current_text,proposed_text,rationale")
            .eq("status", "approved")
            .order("decided_at", desc=True)
            .limit(limit)
        )
        if target_agent_name:
            q = q.eq("target_agent_name", target_agent_name)
        rows = q.execute().data or []
        return rows
    except Exception as exc:  # noqa: BLE001
        log.warning("trainer_fetch_past_rewrites_failed", error=str(exc))
        return []


def _format_failure_samples_block(samples: list[Any], max_n: int = 10) -> str:
    """Render the failure cohort as a readable LLM input block."""
    if not samples:
        return "(no failure samples available in window)"
    lines = []
    for i, s in enumerate(samples[:max_n], start=1):
        err_class = getattr(s, "error_class", None) or "—"
        err_msg = (getattr(s, "error_message", None) or "—")[:300]
        inp = (getattr(s, "rendered_input_text", "") or "")[:400]
        resp = (getattr(s, "original_response", "") or "")[:400]
        lines.append(
            f"  [{i}] error_class={err_class}\n"
            f"      error_message: {err_msg!r}\n"
            f"      input:    {inp!r}\n"
            f"      response: {resp!r}"
        )
    return "\n".join(lines)


def _format_success_samples_block(samples: list[Any], max_n: int = 3) -> str:
    """Render the anti-regression cohort. Smaller (3) on purpose — the
    LLM doesn't need 30 here; the shadow eval already replays 30."""
    if not samples:
        return "(no success samples available)"
    lines = []
    for i, s in enumerate(samples[:max_n], start=1):
        inp = (getattr(s, "rendered_input_text", "") or "")[:400]
        resp = (getattr(s, "original_response", "") or "")[:400]
        lines.append(
            f"  [{i}] input:    {inp!r}\n"
            f"      response: {resp!r}"
        )
    return "\n".join(lines)


def _format_load_bearing_block(clauses: list[str]) -> str:
    if not clauses:
        return "(no load-bearing clauses detected — your rewrite has wide latitude)"
    return "\n".join(f"  - {c}" for c in clauses)


def _format_past_examples_block(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no past founder-approved rewrites yet — this is an early run)"
    lines = []
    for i, r in enumerate(rows, start=1):
        cur = (r.get("current_text") or "")[:300]
        new = (r.get("proposed_text") or "")[:300]
        rat = (r.get("rationale") or "")[:300]
        lines.append(
            f"  [{i}] {r.get('target_agent_name')} / {r.get('prompt_constant_name')}:\n"
            f"      WAS:  {cur!r}\n"
            f"      NOW:  {new!r}\n"
            f"      WHY:  {rat!r}"
        )
    return "\n".join(lines)


def generate_improvement(
    router: ClaudeRouter,
    *,
    agent_name: str,
    prompt_constant_name: str,
    current_text: str,
    failure_samples: list[Any],          # list of shadow_eval.InvocationSample
    success_samples: list[Any],          # list of shadow_eval.InvocationSample
    failure_clusters_block: str,         # pre-formatted by clustering.ClusterReport.format_for_llm
) -> PromptProposal | None:
    """One Claude (HEAVY tier) round-trip per (agent, prompt_constant).

    Wave 5 v2.5: takes structured evidence (samples + clusters) instead
    of a single `failure_signal` string. The LLM gets concrete inputs/
    responses to reason from, plus load-bearing clauses extracted from
    the current prompt and past approved rewrites for house-style
    calibration.

    Returns None on:
      * LLM returns malformed JSON
      * deterministic guardrail rejects the proposal
      * rationale fails Pydantic min_length=50
    """
    load_bearing = extract_load_bearing_clauses(current_text)
    past_rewrites = fetch_past_approved_rewrites(target_agent_name=agent_name)

    user_prompt = GENERATE_IMPROVEMENT_USER.format(
        agent_name=agent_name,
        prompt_constant_name=prompt_constant_name,
        current_text=current_text,
        failure_samples_block=_format_failure_samples_block(failure_samples),
        failure_clusters_block=failure_clusters_block,
        success_samples_block=_format_success_samples_block(success_samples),
        load_bearing_clauses_block=_format_load_bearing_block(load_bearing),
        past_good_examples_block=_format_past_examples_block(past_rewrites),
    )

    try:
        resp = router.complete(
            system=TRAINER_SYSTEM,
            prompt=user_prompt,
            tier=Tier.HEAVY,
            max_tokens=4000,           # bumped — meta-prompt is larger now
            temperature=0.2,
            # Tag TRAINER's own LLM calls so the wrapper's invocation
            # logging attributes them correctly (and so next-week's
            # TRAINER run doesn't accidentally analyze itself).
            prompt_constant_name="TRAINER_SYSTEM",
            agent_name="trainer",
            node_name="generate_prompt_improvements",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "trainer_llm_call_failed",
            agent=agent_name,
            constant=prompt_constant_name,
            error=str(exc),
        )
        return None

    data, ok = extract_json_object(resp.get("text", ""))
    if not ok:
        log.warning(
            "trainer_llm_no_json",
            agent=agent_name,
            constant=prompt_constant_name,
        )
        return None

    proposed_text = str(data.get("proposed_text", "")).strip()
    if not proposed_text:
        log.warning("trainer_llm_empty_proposal", agent=agent_name)
        return None

    # Deterministic guardrail — same three checks as v1.
    valid, reason = validate_proposal_text(current_text, proposed_text)
    if not valid:
        log.warning(
            "trainer_proposal_rejected_by_guardrail",
            agent=agent_name,
            constant=prompt_constant_name,
            reason=reason,
        )
        return None

    try:
        return PromptProposal(
            target_agent_name=agent_name,
            prompt_constant_name=prompt_constant_name,
            current_text=current_text,
            current_text_sha256=sha256_hex(current_text),
            proposed_text=proposed_text,
            rationale=str(data.get("rationale", "")).strip(),
            # `expected_impact` and `confidence` are populated by
            # shadow_eval downstream — NOT by the LLM. We seed them
            # with provisional values that get overwritten.
            expected_impact={
                "addresses_clusters": list(data.get("addresses_clusters") or []),
                "preserves_load_bearing": list(data.get("preserves_load_bearing") or []),
            },
            confidence=0.5,  # placeholder; shadow_eval replaces this
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "trainer_proposal_pydantic_failed",
            agent=agent_name,
            constant=prompt_constant_name,
            error=str(exc),
        )
        return None


# ─────────────────────────── Node 4: persistence ──────────────────────

def persist_proposal(
    proposal: PromptProposal,
    *,
    run_id: str,
    correlation_id: str | None,
    iso_week: str,
) -> str | None:
    """Upsert into `prompt_improvements`. Idempotent on (agent, constant, iso_week).

    Returns the improvement_id on insert, None on a deduped or failed
    upsert. The DB UNIQUE constraint is the second line of defense;
    `generate_key` produces the deterministic key on the Python side.
    """
    key = generate_key(
        scope="prompt_improvement",
        payload={
            "agent": proposal.target_agent_name,
            "constant": proposal.prompt_constant_name,
            "iso_week": iso_week,
        },
        window="none",
    )
    row: dict[str, Any] = {
        "run_id": run_id,
        "correlation_id": correlation_id,
        "iso_week": iso_week,
        "target_agent_name": proposal.target_agent_name,
        "prompt_constant_name": proposal.prompt_constant_name,
        "current_text_sha256": proposal.current_text_sha256,
        "current_text": proposal.current_text,
        "proposed_text": proposal.proposed_text,
        "rationale": proposal.rationale,
        "expected_impact": proposal.expected_impact,
        "confidence": round(float(proposal.confidence), 2),
        "idempotency_key": key,
        "status": "pending",
    }
    try:
        resp = (
            supabase.table("prompt_improvements")
            .upsert(row, on_conflict="idempotency_key", ignore_duplicates=True)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            log.info(
                "trainer_proposal_dedup",
                agent=proposal.target_agent_name,
                constant=proposal.prompt_constant_name,
                iso_week=iso_week,
            )
            return None
        improvement_id = rows[0].get("improvement_id")
        log.info(
            "trainer_proposal_persisted",
            improvement_id=improvement_id,
            agent=proposal.target_agent_name,
            constant=proposal.prompt_constant_name,
        )
        return improvement_id
    except Exception as exc:  # noqa: BLE001
        log.error(
            "trainer_proposal_persist_failed",
            agent=proposal.target_agent_name,
            error=str(exc),
        )
        return None


def update_proposal_decision(
    improvement_id: str,
    *,
    decision: str,                  # 'approved' | 'rejected'
    founder_notes: str | None = None,
) -> None:
    """Called from Node 4 after interrupt() resumes with founder decisions."""
    if decision not in ("approved", "rejected"):
        log.warning("trainer_decision_unknown", decision=decision)
        return
    try:
        supabase.table("prompt_improvements").update({
            "status": decision,
            "founder_notes": founder_notes,
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }).eq("improvement_id", improvement_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "trainer_decision_write_failed",
            improvement_id=improvement_id,
            error=str(exc),
        )


__all__ = [
    "TRAINER_TARGET_AGENTS",
    "iso_week_key",
    "sha256_hex",
    "fetch_performance_window",
    "identify_underperformers",
    "read_agent_prompts",
    "validate_proposal_text",
    "extract_load_bearing_clauses",
    "fetch_past_approved_rewrites",
    "generate_improvement",
    "persist_proposal",
    "update_proposal_decision",
    # thresholds exposed so tests can inject custom values
    "FAILURE_RATE_THRESHOLD",
    "COST_VARIANCE_THRESHOLD",
    "REJECTION_RATIO_THRESHOLD",
    "REGRESSION_FLAGS_THRESHOLD",
]


# ─────────────────────────── Apply (RSI loop close) ───────────────────
# Writes an APPROVED prompt improvement into the agent's prompts.py and audit-logs
# it so AUDITOR can verify the founder approval (Rule 3). Strongly guarded.

def _as_triple_quoted(text: str) -> str:
    body = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return '"""' + body + '"""'


def _write_prompt_audit_log(agent_name: str, target_resource: str, diff_summary: str,
                            before_content: str, hitl_review_id: str | None,
                            current_sha: str | None) -> None:
    try:
        supabase.table("audit_log").insert({
            "source_agent": "trainer",
            "action_type": "prompt_update",
            "target_resource": target_resource,
            "diff_summary": diff_summary,
            "before_content": before_content[:6000],
            "raw_payload": {"agent": agent_name, "current_sha256": current_sha},
            "hitl_review_id": hitl_review_id,
        }).execute()
    except Exception as exc:  # noqa: BLE001 — audit write must not block the apply
        log.warning("trainer_apply_audit_log_failed", agent=agent_name, error=str(exc))


def apply_prompt_update(*, agent_name: str, prompt_constant_name: str, proposed_text: str,
                        expected_sha256: str | None = None,
                        hitl_review_id: str | None = None) -> dict[str, Any]:
    """Apply an approved prompt improvement to prompts.py. Returns {applied, ...}.

    Guards (any failing → no write):
      - constant must be a SIMPLE string literal (skip BinOp/f-string concatenations
        like `RULES + \"\"\"...\"\"\"` — those need manual apply to avoid dropping prefixes)
      - SHA guard: current value must match the proposal's basis (not stale)
      - the rewritten file MUST compile (else not written)
      - original is backed up before writing; audit_log row records the founder review
    """
    slug = _DIR_SLUG_BY_DB_NAME.get(agent_name, agent_name)
    path = _AGENTS_DIR / slug / "prompts.py"
    if not path.exists():
        return {"applied": False, "error": f"prompts.py not found for {agent_name}"}

    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {"applied": False, "error": f"prompts.py unparseable: {exc}"}

    value_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == prompt_constant_name for t in node.targets
        ):
            value_node = node.value
            break
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == prompt_constant_name:
            value_node = node.value
            break
    if value_node is None:
        return {"applied": False, "error": f"constant {prompt_constant_name} not found"}

    # Only simple string literals are safe to auto-rewrite.
    if not (isinstance(value_node, ast.Constant) and isinstance(value_node.value, str)):
        return {"applied": False, "error": "non-literal prompt (concatenation/f-string) — manual apply required"}

    current_value = value_node.value
    if expected_sha256 and sha256_hex(current_value) != expected_sha256:
        return {"applied": False, "error": "stale: current prompt changed since proposal"}

    line_starts = [0]
    for line in src.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    start = line_starts[value_node.lineno - 1] + value_node.col_offset
    end = line_starts[value_node.end_lineno - 1] + value_node.end_col_offset

    new_src = src[:start] + _as_triple_quoted(proposed_text) + src[end:]
    try:
        compile(new_src, str(path), "exec")
    except SyntaxError as exc:
        return {"applied": False, "error": f"rewrite produced invalid Python: {exc}"}

    backup_path = f"{path}.bak.{int(datetime.now(timezone.utc).timestamp())}"
    Path(backup_path).write_text(src, encoding="utf-8")
    path.write_text(new_src, encoding="utf-8")

    diff_summary = f"{prompt_constant_name}: {len(current_value)}→{len(proposed_text)} chars"
    _write_prompt_audit_log(agent_name, str(path), diff_summary, src, hitl_review_id, expected_sha256)
    log.info("trainer_prompt_applied", agent=agent_name, constant=prompt_constant_name,
             backup=backup_path)

    # Push the rewritten file to GitHub so the change survives Railway redeploys.
    # Railway containers are rebuilt from the git image on every deploy; a local-only
    # write is erased on the next deploy, silently reverting the approved rewrite.
    # The GitHub commit IS the durable apply — local write is only the staging step.
    commit_result = _commit_prompt_update_to_github(
        agent_name=agent_name,
        prompt_constant_name=prompt_constant_name,
        file_path=path,
        new_content=new_src,
        diff_summary=diff_summary,
        hitl_review_id=hitl_review_id,
    )
    if not commit_result.get("committed"):
        log.warning(
            "trainer_github_commit_failed",
            agent=agent_name,
            constant=prompt_constant_name,
            error=commit_result.get("error"),
        )
        # Do not fail the apply — local write is still correct for the running process.
        # The next Railway deploy will revert unless this is fixed, but the current
        # session benefits from the patched prompt immediately.

    return {
        "applied": True,
        "backup_path": backup_path,
        "diff_summary": diff_summary,
        "github_committed": commit_result.get("committed", False),
        "github_sha": commit_result.get("sha"),
        "error": None,
    }


def _commit_prompt_update_to_github(
    *,
    agent_name: str,
    prompt_constant_name: str,
    file_path: "Path",
    new_content: str,
    diff_summary: str,
    hitl_review_id: str | None,
) -> dict:
    """Push the rewritten prompts.py to the main branch via GitHub API.

    Idempotent via effect_log: if a row for this (agent, constant, content_hash)
    already exists, returns the stored SHA without re-calling the GitHub API.
    Writes the idempotency record IMMEDIATELY after a successful GitHub commit.
    Returns {committed: bool, sha: str|None, error: str|None}.
    """
    import hashlib

    from omerion_core.clients.supabase_client import supabase as _supa

    content_hash = hashlib.sha256(new_content.encode()).hexdigest()[:16]
    op_key = f"trainer_commit:{agent_name}:{prompt_constant_name}:{content_hash}"

    # Idempotency check: was this exact content already committed?
    try:
        existing_log = (
            _supa.table("effect_log")
            .select("result")
            .eq("idempotency_key", op_key)
            .maybe_single()
            .execute()
        )
        if existing_log.data:
            return {"committed": True, "sha": existing_log.data["result"].get("sha"), "error": None}
    except Exception as exc:
        log.warning("effect_log_check_failed", op_key=op_key, error=str(exc))

    try:
        from omerion_core.clients.github_client import github_client as _get_github_client

        gh = _get_github_client()
        repo_name = settings.github_build_repo
        if not repo_name:
            return {"committed": False, "error": "github_build_repo not configured"}

        repo = gh.get_repo(repo_name)
        try:
            rel_path = file_path.relative_to(file_path.parents[4])
        except (ValueError, IndexError):
            rel_path = Path("omerion") / "agents" / agent_name / "prompts.py"

        commit_message = (
            f"trainer: patch {prompt_constant_name} in {agent_name}\n\n"
            f"{diff_summary}\n"
            + (f"hitl_review_id: {hitl_review_id}" if hitl_review_id else "")
        )

        try:
            existing_file = repo.get_contents(str(rel_path), ref="main")
            result = repo.update_file(
                path=str(rel_path),
                message=commit_message,
                content=new_content,
                sha=existing_file.sha,
                branch="main",
            )
        except Exception:
            result = repo.create_file(
                path=str(rel_path),
                message=commit_message,
                content=new_content,
                branch="main",
            )

        committed_sha = result["commit"].sha

        # Write idempotency record IMMEDIATELY after successful GitHub commit.
        try:
            _supa.table("effect_log").insert({
                "idempotency_key": op_key,
                "effect_type": "github_commit",
                "result": {"sha": committed_sha},
            }).execute()
        except Exception as exc:
            log.warning("effect_log_insert_failed", op_key=op_key, error=str(exc))

        return {"committed": True, "sha": committed_sha, "error": None}
    except Exception as exc:
        return {"committed": False, "sha": None, "error": str(exc)}
