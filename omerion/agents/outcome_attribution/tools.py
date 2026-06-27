"""Tools for Outcome Attribution.

All numeric work is deterministic SQL/aggregation against Supabase.
Claude is used for the narrative summary and feedback item generation only.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.embeddings import embed
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings

from .prompts import (
    FEEDBACK_SYSTEM,
    FEEDBACK_USER,
    SUMMARY_SYSTEM,
    SUMMARY_USER,
)
from .state import FeedbackItem, KpiDelta

log = get_logger("omerion.agents.outcome_attribution")

_EPS = 1e-9


def load_deployment(deployment_id: UUID) -> dict[str, Any]:
    resp = (
        supabase.table("deployments")
        .select("deployment_id,blueprint_id,client_id,opportunity_id,go_live_date,status,modules_deployed")
        .eq("deployment_id", str(deployment_id))
        .single()
        .execute()
    )
    return resp.data or {}


def persona_for(client_id: UUID | None) -> str:
    """Best-effort persona lookup via the primary contact on the client's account."""
    if client_id is None:
        return "unknown"
    resp = (
        supabase.table("contacts")
        .select("persona")
        .eq("account_id", str(client_id))
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return (rows[0].get("persona") if rows else None) or "unknown"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _window(go_live_at: str, window_days: int) -> tuple[str, str, str, str]:
    if not go_live_at:
        raise ValueError("go_live_at is required to compute attribution window")
    try:
        anchor = datetime.fromisoformat(go_live_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"go_live_at is not a valid ISO datetime: {go_live_at!r}") from exc
    pre_start = anchor - timedelta(days=window_days)
    post_end = anchor + timedelta(days=window_days)
    return _iso(pre_start), _iso(anchor), _iso(anchor), _iso(post_end)


def sum_revenue(client_id: UUID | None, start: str, end: str) -> float:
    if client_id is None:
        return 0.0
    resp = (
        supabase.table("revenue_events")
        .select("amount_usd")
        .eq("client_id", str(client_id))
        .gte("occurred_at", start)
        .lt("occurred_at", end)
        .execute()
    )
    return float(sum((r.get("amount_usd") or 0) for r in (resp.data or [])))


def conversion_rate(client_id: UUID | None, start: str, end: str) -> tuple[float, int]:
    """closed_won / (closed_won + closed_lost) over the window."""
    if client_id is None:
        return 0.0, 0
    q = (
        supabase.table("lead_conversions")
        .select("to_stage")
        .eq("client_id", str(client_id))
        .gte("converted_at", start)
        .lt("converted_at", end)
        .execute()
    )
    rows = q.data or []
    won = sum(1 for r in rows if r.get("to_stage") == "closed_won")
    lost = sum(1 for r in rows if r.get("to_stage") == "closed_lost")
    total = won + lost
    return (won / total if total else 0.0), total


def _kpi_sample(persona: str, kpi: str, client_id: UUID | None, start: str, end: str) -> tuple[float, int]:
    """Pull KPI samples from agent_telemetry `metrics` JSONB keyed by kpi name.

    Telemetry rows carry `metrics={kpi_name: value}` written by the agent
    that owns the KPI (e.g. CRM Nurture writes speed_to_lead_minutes).
    """
    q = supabase.table("agent_telemetry").select("metrics").gte("occurred_at", start).lt("occurred_at", end)
    if client_id is not None:
        q = q.eq("client_id", str(client_id))
    resp = q.execute()
    values: list[float] = []
    for row in resp.data or []:
        m = row.get("metrics") or {}
        v = m.get(kpi)
        if isinstance(v, (int, float)):
            values.append(float(v))
    if not values:
        return 0.0, 0
    return sum(values) / len(values), len(values)


def compute_deltas(
    persona: str,
    client_id: UUID | None,
    go_live_at: str,
    window_days: int,
) -> list[KpiDelta]:
    cfg = settings.agent("outcome_attribution")
    kpi_map: dict[str, list[str]] = cfg.get("kpi_definitions", {})
    threshold = float(cfg.get("min_delta_threshold", 0.10))
    kpis = kpi_map.get(persona, [])
    if not kpis:
        return []

    pre_start, pre_end, post_start, post_end = _window(go_live_at, window_days)
    deltas: list[KpiDelta] = []
    for kpi in kpis:
        pre_mean, pre_n = _kpi_sample(persona, kpi, client_id, pre_start, pre_end)
        post_mean, post_n = _kpi_sample(persona, kpi, client_id, post_start, post_end)
        delta_abs = post_mean - pre_mean
        delta_pct = delta_abs / (abs(pre_mean) + _EPS) if pre_n else 0.0
        deltas.append(KpiDelta(
            name=kpi,
            pre_mean=round(pre_mean, 4),
            post_mean=round(post_mean, 4),
            delta_abs=round(delta_abs, 4),
            delta_pct=round(delta_pct, 4),
            sample_pre=pre_n,
            sample_post=post_n,
            significant=pre_n > 0 and post_n > 0 and abs(delta_pct) >= threshold,
        ))
    return deltas


def render_summary(
    router: ClaudeRouter,
    *,
    deployment_id: UUID,
    persona: str,
    window_days: int,
    threshold: float,
    deltas: list[KpiDelta],
    rev_pre: float,
    rev_post: float,
    cr_pre: float,
    cr_post: float,
) -> str:
    resp = router.complete(
        system=SUMMARY_SYSTEM,
        prompt=SUMMARY_USER.format(
            deployment_id=str(deployment_id),
            persona=persona,
            window_days=window_days,
            threshold=threshold,
            deltas_json=json.dumps([d.model_dump() for d in deltas])[:6000],
            rev_pre=round(rev_pre, 2),
            rev_post=round(rev_post, 2),
            cr_pre=round(cr_pre, 4),
            cr_post=round(cr_post, 4),
        ),
        tier=Tier.DEFAULT,
        max_tokens=500,
    )
    return (resp["text"] or "").strip()


def derive_proof_point(deltas: list[KpiDelta]) -> str:
    """Pick the best significant delta as a one-line proof point."""
    sig = [d for d in deltas if d.significant]
    if not sig:
        return ""
    best = max(sig, key=lambda d: abs(d.delta_pct))
    sign = "+" if best.delta_abs >= 0 else ""
    return f"{best.name}: {sign}{best.delta_pct * 100:.1f}% ({best.pre_mean} → {best.post_mean})"


_ARRAY_RE = re.compile(r"\[\s*(?:\{.*?\}\s*,?\s*)*\]", re.DOTALL)


def _extract_json_array(raw: str) -> list[dict[str, Any]]:
    if not raw:
        return []
    match = _ARRAY_RE.search(raw)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


_ALLOWED_TARGETS = {"icp_scoring_weights", "offer_templates", "rd_backlog"}


def generate_feedback(
    router: ClaudeRouter,
    *,
    deployment_id: UUID,
    persona: str,
    summary_md: str,
    deltas: list[KpiDelta],
) -> list[FeedbackItem]:
    if not any(d.significant for d in deltas):
        return []
    resp = router.complete(
        system=FEEDBACK_SYSTEM,
        prompt=FEEDBACK_USER.format(
            deployment_id=str(deployment_id),
            persona=persona,
            summary_md=summary_md[:3000],
            deltas_json=json.dumps([d.model_dump() for d in deltas])[:6000],
        ),
        tier=Tier.DEFAULT,
        max_tokens=800,
    )
    items: list[FeedbackItem] = []
    targets_cfg = set(settings.agent("outcome_attribution").get("feedback_targets", [])) or _ALLOWED_TARGETS
    for entry in _extract_json_array(resp["text"]):
        target = str(entry.get("target", "")).strip()
        if target not in targets_cfg:
            continue
        try:
            items.append(FeedbackItem(
                target=target,
                recommendation=str(entry.get("recommendation", "")).strip(),
                rationale=str(entry.get("rationale", "")).strip(),
                confidence=float(entry.get("confidence", 0.5)),
            ))
        except (TypeError, ValueError):
            continue
    return items[:4]


def write_report(
    *,
    deployment_id: UUID,
    client_id: UUID | None,
    deltas: list[KpiDelta],
    summary_md: str,
    proof_point: str,
    window_days: int,
) -> UUID | None:
    cfg = settings.agent("outcome_attribution")
    kpi_deltas_json = {d.name: d.model_dump() for d in deltas}
    resp = supabase.table("attribution_reports").upsert({
        "deployment_id": str(deployment_id),
        "client_id": str(client_id) if client_id else None,
        "kpi_deltas": kpi_deltas_json,
        "summary": summary_md,
        "proof_point": proof_point,
        "attribution_model": cfg.get("attribution_model", "pre_post_simple"),
        "window_days": window_days,
    }, on_conflict="deployment_id").execute()
    row = (resp.data or [{}])[0]
    return UUID(row["report_id"]) if row.get("report_id") else None


def write_feedback(deployment_id: UUID, items: list[FeedbackItem]) -> int:
    if not items:
        return 0
    rows = [{
        "agent_name": "outcome_attribution",
        "kind": "feedback",
        "subject": f"{item.target} — deployment {deployment_id}",
        "body_md": f"**Recommendation:** {item.recommendation}\n\n**Rationale:** {item.rationale}\n\n**Target:** `{item.target}`",
        "metadata": {
            "target": item.target,
            "confidence": item.confidence,
            "deployment_id": str(deployment_id),
        },
    } for item in items]
    # Simple INSERT: generated_drafts stores draft content per agent run.
    # Re-runs naturally create new rows; prior runs are historical records.
    supabase.table("generated_drafts").insert(rows).execute()
    return len(rows)


_OUTCOMES_NS = "delivery_outcomes"
_DEDUP_HARD = 0.96
_DEDUP_SOFT = 0.90


def embed_outcome(
    *,
    report_id: UUID,
    deployment_id: UUID,
    client_id: UUID | None,
    persona: str,
    service_package: str,
    summary_md: str,
    proof_point: str,
    revenue_post: float,
    kpi_count: int,
    significant_count: int,
    delta_pct_max: float,
    proof_point_kpi: str,
    run_date: str,
) -> bool:
    """Embed the attribution report summary into the delivery_outcomes Pinecone namespace.

    Only called when significant_count >= 1 — zero-delta reports are not useful
    proof-points and would pollute proposal retrieval context.

    Returns True if upserted, False if skipped (dedup) or on error.
    Failures are logged but never raised — emit_node must not be blocked.
    """
    if significant_count < 1:
        return False

    embed_text = f"{summary_md.strip()} | {proof_point.strip()}"[:700]
    try:
        vec = embed(embed_text)
        idx = pinecone_index()

        # Dedup: query for close existing vectors from this deployment.
        result = idx.query(
            vector=vec,
            top_k=1,
            include_metadata=False,
            filter={"agent_id": {"$eq": "outcome_attribution"}, "deployment_id": {"$eq": str(deployment_id)}},
            namespace=_OUTCOMES_NS,
        )
        if result.matches:
            score = result.matches[0].score
            if score >= _DEDUP_HARD:
                return False  # hard skip — same report re-embedded

        metadata: dict = {
            "agent_id": "outcome_attribution",
            "department": "delivery",
            "namespace": _OUTCOMES_NS,
            "run_date": run_date,
            "report_id": str(report_id),
            "deployment_id": str(deployment_id),
            "client_id": str(client_id) if client_id else "",
            "persona": persona,
            "service_package": service_package,
            "revenue_post": revenue_post,
            "kpi_count": kpi_count,
            "significant_count": significant_count,
            "delta_pct_max": delta_pct_max,
            "proof_point_kpi": proof_point_kpi,
            "text": embed_text[:500],
        }
        if result.matches and result.matches[0].score >= _DEDUP_SOFT:
            metadata["is_apparent_duplicate"] = True

        idx.upsert(
            vectors=[{"id": f"outcome:{deployment_id}", "values": vec, "metadata": metadata}],
            namespace=_OUTCOMES_NS,
        )
        return True
    except Exception as exc:
        from omerion_core.logging import get_logger
        get_logger("omerion.agents.outcome_attribution").warning(
            "embed_outcome_failed", deployment_id=str(deployment_id), error=str(exc)
        )
        return False
