"""Tools for R3 Strategic Architect."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger

from .prompts import SYNTHESIZE_SYSTEM, SYNTHESIZE_USER
from .state import DesignProposal, SignalBundle

log = get_logger("omerion.agents.r3_strategic_architect")

_VALID_MODULES = {"daam", "capa", "remi", "asap", "internal_os"}
_VALID_IMPACT = {"low", "medium", "high"}
_VALID_EFFORT = {"S", "M", "L", "XL"}

_IMPACT_WEIGHT = {"low": 0.3, "medium": 0.6, "high": 1.0}
_EFFORT_WEIGHT = {"S": 1.0, "M": 0.75, "L": 0.5, "XL": 0.25}


def _since(lookback_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()


def load_signals(lookback_days: int) -> SignalBundle:
    since = _since(lookback_days)
    rd = supabase.table("rd_insights").select(
        "insight_id,title,summary,impact_tag,estimated_priority,source_url,created_at"
    ).gte("created_at", since).limit(50).execute()
    oss = supabase.table("rd_oss_candidates").select(
        "candidate_id,name,repo_url,integration_type,impact_tag,overall_score,"
        "rubric_fit,rubric_risk,rescore_history,recommendation,created_at"
    ).gte("created_at", since).gte("rubric_fit", 0.5).lt("rubric_risk", 0.7).order(
        "overall_score", desc=True
    ).limit(20).execute()
    reports = supabase.table("attribution_reports").select(
        "report_id,deployment_id,persona,proof_point,kpi_deltas,created_at"
    ).gte("created_at", since).limit(20).execute()
    return SignalBundle(
        rd_insights=list(rd.data or []),
        oss_candidates=list(oss.data or []),
        attribution_reports=list(reports.data or []),
    )


def _fmt_insights(rows: list[dict]) -> str:
    if not rows:
        return "(none)"
    return "\n".join(
        f"- [{r['insight_id']}] ({r.get('impact_tag','?')}/{r.get('estimated_priority','?')}) "
        f"{r.get('title','')} — {(r.get('summary') or '')[:160]}"
        for r in rows
    )


def _maturity_trend(history: list) -> str:
    """Return 'rising', 'stable', or 'single' based on rescore_history."""
    if not history or len(history) < 2:
        return "single"
    try:
        first = float(history[0].get("maturity", 0))
        last = float(history[-1].get("maturity", 0))
        return "rising" if last - first >= 0.05 else "stable"
    except (TypeError, ValueError, AttributeError):
        return "stable"


def _fmt_oss(rows: list[dict]) -> str:
    if not rows:
        return "(none)"
    parts = []
    for r in rows:
        history = r.get("rescore_history") or []
        trend = _maturity_trend(history)
        trend_flag = " [maturity:rising]" if trend == "rising" else ""
        parts.append(
            f"- [{r['candidate_id']}] {r.get('name','')} ({r.get('integration_type','?')}) "
            f"score={r.get('overall_score')}{trend_flag} → {r.get('recommendation','')[:120]}"
        )
    return "\n".join(parts)


def _fmt_reports(rows: list[dict]) -> str:
    if not rows:
        return "(none)"
    out = []
    for r in rows:
        proof = r.get("proof_point") or ""
        deltas = r.get("kpi_deltas") or []
        sig = [d for d in deltas if isinstance(d, dict) and d.get("significant")]
        out.append(
            f"- [{r['report_id']}] persona={r.get('persona','?')} proof={proof} "
            f"significant_deltas={len(sig)}"
        )
    return "\n".join(out)


_JSON_ARR_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json_array(raw: str) -> list:
    if not raw:
        return []
    m = _JSON_ARR_RE.search(raw)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def priority_score(impact: str, effort: str) -> float:
    return round(_IMPACT_WEIGHT.get(impact, 0.0) * _EFFORT_WEIGHT.get(effort, 0.0), 3)


def _filter_ids(ids: list, valid: set[str]) -> list[str]:
    return [str(i) for i in (ids or []) if str(i) in valid]


def synthesize_proposals(
    router: ClaudeRouter,
    signals: SignalBundle,
    lookback_days: int,
    run_date: str,
    prior_block: str = "(none)",
) -> list[DesignProposal]:
    if not (signals.rd_insights or signals.oss_candidates or signals.attribution_reports):
        return []
    resp = router.complete(
        system=SYNTHESIZE_SYSTEM,
        prompt=SYNTHESIZE_USER.format(
            lookback_days=lookback_days,
            run_date=run_date,
            insights_block=_fmt_insights(signals.rd_insights),
            oss_block=_fmt_oss(signals.oss_candidates),
            attribution_block=_fmt_reports(signals.attribution_reports),
            prior_block=prior_block,
        ),
        tier=Tier.HEAVY,
        max_tokens=2500,
        temperature=0.2,
    )
    raw = _extract_json_array(resp["text"])
    valid_insight_ids = {str(r.get("insight_id")) for r in signals.rd_insights}
    valid_oss_ids = {str(r.get("candidate_id")) for r in signals.oss_candidates}
    valid_report_ids = {str(r.get("report_id")) for r in signals.attribution_reports}

    out: list[DesignProposal] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        module = str(item.get("target_module", "")).lower()
        impact = str(item.get("impact", "")).lower()
        effort = str(item.get("effort", "")).upper()
        if module not in _VALID_MODULES or impact not in _VALID_IMPACT or effort not in _VALID_EFFORT:
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        out.append(DesignProposal(
            title=title[:120],
            problem_statement=str(item.get("problem_statement", "")).strip(),
            hypothesis=str(item.get("hypothesis", "")).strip(),
            design_doc_md=str(item.get("design_doc_md", "")).strip(),
            target_module=module,  # type: ignore[arg-type]
            impact=impact,  # type: ignore[arg-type]
            effort=effort,  # type: ignore[arg-type]
            priority_score=float(item.get("priority_score") or 0.0) or priority_score(impact, effort),
            supporting_insight_ids=_filter_ids(item.get("supporting_insight_ids", []), valid_insight_ids),
            supporting_oss_ids=_filter_ids(item.get("supporting_oss_ids", []), valid_oss_ids),
            supporting_report_ids=_filter_ids(item.get("supporting_report_ids", []), valid_report_ids),
            blueprint_handoff=item.get("blueprint_handoff", {}) if isinstance(item.get("blueprint_handoff"), dict) else {},
        ))
    out.sort(key=lambda p: p.priority_score, reverse=True)
    return out


def write_proposal(p: DesignProposal) -> UUID | None:
    from datetime import date as _date
    run_date = str(_date.today())

    row = {
        "title": p.title[:120],
        "problem_statement": p.problem_statement,
        "hypothesis": p.hypothesis,
        "design_doc_md": p.design_doc_md,
        "target_module": p.target_module,
        "impact": p.impact,
        "effort": p.effort,
        "priority_score": p.priority_score,
        "supporting_insight_ids": p.supporting_insight_ids,
        "supporting_oss_ids": p.supporting_oss_ids,
        "supporting_report_ids": p.supporting_report_ids,
        "blueprint_handoff": p.blueprint_handoff,
        "status": "proposed",
        "run_date": run_date,
    }

    # Race condition fix (TOCTOU): a select-then-insert could let two concurrent
    # runs both see "no row" and both INSERT — and migration 0051 already enforces
    # UNIQUE(title, run_date) via uq_rd_proposals_title_run_date, so the loser would
    # raise a 23505 unique-violation mid-persist. Instead cooperate with that index:
    # ON CONFLICT DO NOTHING (ignore_duplicates) makes the write a clean idempotent
    # no-op on replay/concurrency, then we fetch the surviving row's id.
    resp = (
        supabase.table("rd_proposals")
        .upsert(row, on_conflict="title,run_date", ignore_duplicates=True)
        .execute()
    )
    if resp.data:
        return UUID(resp.data[0]["proposal_id"])

    # Conflict path: row already existed (concurrent run / replay) → fetch its id.
    existing = (
        supabase.table("rd_proposals")
        .select("proposal_id")
        .eq("title", p.title[:120])
        .eq("run_date", run_date)
        .limit(1)
        .execute()
    )
    if existing.data:
        return UUID(existing.data[0]["proposal_id"])
    return None


def mark_proposal_decision(proposal_id: UUID, decision: str) -> None:
    supabase.table("rd_proposals").update({
        "status": "approved" if decision == "approved" else "rejected",
    }).eq("proposal_id", str(proposal_id)).execute()


# ── Signal clustering — prevents R3 context overflow ──────────────────────────
# At 50 insights × 160 chars, a flat synthesize call burns ~11k context chars
# before the system prompt. Clustering by impact_tag (5 buckets) keeps each
# synthesis call to ≤10 insights + ≤4 OSS candidates — well within efficient range.

_ALL_TAGS = ("daam", "capa", "remi", "asap", "internal_os")
_FALLBACK_TAG = "internal_os"


def cluster_signals_by_tag(signals: SignalBundle) -> dict[str, SignalBundle]:
    """Group rd_insights and oss_candidates into per-impact_tag SignalBundles.

    attribution_reports are cross-cluster — copied into each bucket.
    Insights/candidates with no recognised tag go into the _FALLBACK_TAG bucket.
    """
    buckets: dict[str, SignalBundle] = {}

    for insight in signals.rd_insights:
        tag = str(insight.get("impact_tag") or "").lower()
        if tag not in _ALL_TAGS:
            tag = _FALLBACK_TAG
        if tag not in buckets:
            buckets[tag] = SignalBundle(attribution_reports=signals.attribution_reports)
        buckets[tag].rd_insights.append(insight)

    for oss in signals.oss_candidates:
        tag = str(oss.get("impact_tag") or "").lower()
        if tag not in _ALL_TAGS:
            tag = _FALLBACK_TAG
        if tag not in buckets:
            buckets[tag] = SignalBundle(attribution_reports=signals.attribution_reports)
        buckets[tag].oss_candidates.append(oss)

    return buckets


def synthesize_proposals_clustered(
    router: ClaudeRouter,
    signals: SignalBundle,
    lookback_days: int,
    run_date: str,
    prior_block: str = "(none)",
) -> list[DesignProposal]:
    """Cluster signals by impact_tag, synthesize per cluster, merge results.

    Each cluster gets its own synthesize_proposals() call with focused context.
    Proposals are deduplicated by title before returning.
    """
    if not (signals.rd_insights or signals.oss_candidates):
        return []

    clusters = cluster_signals_by_tag(signals)
    all_proposals: list[DesignProposal] = []
    seen_titles: set[str] = set()

    for tag, cluster_bundle in clusters.items():
        if not (cluster_bundle.rd_insights or cluster_bundle.oss_candidates):
            continue
        try:
            proposals = synthesize_proposals(
                router=router,
                signals=cluster_bundle,
                lookback_days=lookback_days,
                run_date=run_date,
                prior_block=prior_block,
            )
            for p in proposals:
                if p.title not in seen_titles:
                    seen_titles.add(p.title)
                    all_proposals.append(p)
        except Exception as exc:  # noqa: BLE001
            log.warning("r3_cluster_synthesis_failed", tag=tag, error=str(exc))

    all_proposals.sort(key=lambda p: p.priority_score, reverse=True)
    return all_proposals
