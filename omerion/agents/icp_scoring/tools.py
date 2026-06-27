"""Tools for ICP scoring.

Sub-scores are computed deterministically from structured data; Claude
is only invoked for intent *explanations* and the final digest.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable
from uuid import UUID

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.http.circuit_breaker import BREAKER
from omerion_core.llm.embeddings import embed
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.rate_limit import rate_limited
from omerion_core.rate_limit.token_bucket import BUCKETS
from omerion_core.retry import transient_retry
from omerion_core.settings import settings
from omerion_core.util.time import parse_iso_utc

from .prompts import (
    DIGEST_SYSTEM,
    DIGEST_USER,
    INTENT_EXPLANATION_SYSTEM,
    INTENT_EXPLANATION_USER,
)
from .state import ScoredContact

log = get_logger("omerion.agents.icp_scoring")

_TIER_SCORE = {1: 1.0, 2: 0.7, 3: 0.4}

# Circuit breaker: after this many consecutive Pinecone failures inside a single
# scoring batch, stop calling Pinecone for the rest of the batch and surface a
# loud warning. Without this, every contact silently scores 0.0 for intent and
# the entire run is biased toward the "cold" segment without any signal that
# Pinecone is the cause.
_RAG_FAILURE_THRESHOLD = 3


class _RagBreaker:
    """Per-batch circuit breaker. Reset by `reset_rag_breaker()` at batch start."""
    consecutive_failures: int = 0
    open: bool = False


def reset_rag_breaker() -> None:
    _RagBreaker.consecutive_failures = 0
    _RagBreaker.open = False


def _persona_tier_score(persona: str) -> float:
    """Look up persona tier from top-level agents.yaml `personas`."""
    p = settings.shared("personas").get(persona) or {}
    return _TIER_SCORE.get(int(p.get("tier", 3)), 0.2)


# Operator-archetype lookup now lives in the shared resolver so the Sales and
# scoring layers can't drift. Re-exported under the private name for callers.
from omerion_core.personas import archetype_for as _archetype_for  # noqa: E402,F401


def _role_seniority(title: str, persona: str) -> float:
    """Prefer exact role-term match; fall back to 'owner/president/partner' heuristic."""
    t = (title or "").lower()
    persona_cfg = settings.shared("personas").get(persona) or {}
    terms = [r.lower() for r in persona_cfg.get("role_terms", [])]
    if terms and any(term in t for term in terms):
        return 1.0
    return 0.8 if any(w in t for w in ("owner", "president", "partner", "principal", "broker")) else 0.5


_TEAM_SIZE_BUCKET_MIDPOINT = {
    "xs": 5, "s": 25, "m": 75, "l": 200, "xl": 600,
}


def _team_size_from_bucket(bucket: str | int | None) -> int:
    if bucket is None:
        return 0
    # Handle integer inputs: map raw employee count to bucket category.
    if isinstance(bucket, int):
        if bucket <= 5:
            bucket = "xs"
        elif bucket <= 20:
            bucket = "s"
        elif bucket <= 50:
            bucket = "m"
        elif bucket <= 200:
            bucket = "l"
        else:
            bucket = "xl"
    elif isinstance(bucket, str):
        bucket = bucket.lower()
    else:
        return 0
    return _TEAM_SIZE_BUCKET_MIDPOINT.get(bucket, 0)


def load_candidates(contact_ids: Iterable[UUID] | None = None, since_days: int = 7) -> list[dict]:
    q = supabase.table("contacts").select(
        "contact_id,account_id,first_name,last_name,role,persona,stage,created_at,last_touch_at,"
        "accounts(name,domain,tier,market,team_size_bucket)"
    )
    if contact_ids:
        q = q.in_("contact_id", [str(c) for c in contact_ids])
    else:
        q = q.gte("updated_at", _ago_iso(since_days))
    resp = q.execute()
    rows = resp.data or []
    # Derive convenience fields the rest of the agent expects.
    for r in rows:
        first = r.get("first_name") or ""
        last = r.get("last_name") or ""
        r["full_name"] = " ".join(filter(None, [first, last])).strip()
        r["title"] = r.get("role") or ""
    return rows


def _ago_iso(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def compute_fit(contact: dict) -> tuple[float, dict[str, float]]:
    cfg = settings.agent("icp_scoring")["fit_weights"]
    account = contact.get("accounts") or {}
    persona = contact.get("persona") or "unknown"
    tier_map = {"A": 1.0, "B": 0.75, "C": 0.5, "D": 0.3}
    employees = _team_size_from_bucket(account.get("team_size_bucket"))
    volume = min(employees / 50.0, 1.0)
    team_size = min(employees / 30.0, 1.0)
    sub = {
        "persona_tier": _persona_tier_score(persona),
        "deal_volume": volume,
        "role_seniority": _role_seniority(contact.get("title", ""), persona),
        "tech_maturity": tier_map.get(account.get("tier") or "C", 0.5),
        "team_size": team_size,
    }
    fit = sum(sub.get(k, 0.0) * w for k, w in cfg.items())
    return fit, sub


def compute_intent(contact: dict) -> tuple[float, dict[str, float]]:
    cfg = settings.agent("icp_scoring")["intent_weights"]
    rag = _rag_intent_score(contact)
    recency = _engagement_recency(contact)
    volume = _engagement_volume(contact)
    sub = {"semantic_pain_match": rag, "engagement_recency": recency, "engagement_volume": volume}
    intent = sum(sub[k] * cfg[k] for k in cfg)
    return intent, sub


_STAGE_ORDER = {
    "new_lead": 0, "contacted": 1, "engaged": 2,
    "proposal_sent": 3, "meeting_booked": 4, "won": 5,
}


def compute_timing(contact: dict) -> tuple[float, dict[str, float]]:
    cfg = settings.agent("icp_scoring")["timing_weights"]
    contact_id = contact.get("contact_id")

    last = contact.get("last_touch_at")
    last_dt = parse_iso_utc(last)
    if last:
        if last_dt is None:
            log.warning("icp_timing_parse_failed", contact_id=contact_id, field="last_touch_at", value=last)
            days_since = 30.0
        else:
            days_since = float((datetime.now(timezone.utc) - last_dt).days)
    else:
        days_since = 30.0

    created = contact.get("created_at")
    created_dt = parse_iso_utc(created)
    if created:
        if created_dt is None:
            log.warning("icp_timing_parse_failed", contact_id=contact_id, field="created_at", value=created)
            created_days: float = 60.0
        else:
            created_days = float(max(1, (datetime.now(timezone.utc) - created_dt).days))
    else:
        created_days = 60.0

    # deal_age: younger contacts score higher (less time to go cold)
    deal_age = max(0.0, 1.0 - min(created_days / 90.0, 1.0))

    # stage_velocity: contacts that progressed to later stages quickly score higher
    stage_idx = _STAGE_ORDER.get(contact.get("stage") or "new_lead", 0)
    stage_velocity = min(1.0, (stage_idx * 14.0) / created_days) if stage_idx > 0 else 0.0

    sub = {
        "days_since_last_touch": max(0.0, 1.0 - min(days_since / 14.0, 1.0)),
        "stage_velocity": stage_velocity,
        "deal_age": deal_age,
    }
    timing = sum(sub[k] * cfg[k] for k in cfg)
    return timing, sub


@rate_limited(BUCKETS["pinecone"])
@transient_retry(attempts=2, min_wait=1, max_wait=8, exceptions=(Exception,))
def _pinecone_query(vector: list, contact_id: str) -> list:
    return pinecone_index().query(
        vector=vector,
        top_k=3,
        namespace="emails",
        filter={"contact_id": {"$eq": contact_id}},
    ).matches


def _rag_intent_score(contact: dict) -> float:
    # rag_query_templates canonical source is now Obsidian vault
    # revenue/icp-scoring/rag-query-templates.md, read by mcp-icp-scoring at
    # startup and stamped into state.scratch["rag_query_templates"]. Fall back
    # to agents.yaml during the transition window before the MCP is deployed.
    template_map = settings.agent("icp_scoring").get("rag_query_templates", {})
    archetype = _archetype_for(contact.get("persona") or "")
    query = template_map.get(archetype, "")
    if not query:
        return 0.0
    # Respect both the per-batch breaker AND the shared process-wide one: if
    # Pinecone is down fleet-wide (other agents already tripped it), skip here too.
    if _RagBreaker.open or BREAKER.is_open("pinecone"):
        return 0.0
    contact_id = str(contact["contact_id"])
    # Embed failure is an OpenAI / network problem — NOT a Pinecone problem.
    # Count it separately so an OpenAI outage doesn't open the Pinecone breaker.
    try:
        vector = embed(query)
    except Exception as exc:
        log.warning("rag_embed_failed", contact_id=contact_id, error=str(exc),
                    error_class=type(exc).__name__)
        return 0.0
    try:
        matches = _pinecone_query(vector, contact_id)
        _RagBreaker.consecutive_failures = 0
        BREAKER.record_success("pinecone")  # feed the shared breaker
        if not matches:
            return 0.0
        return min(1.0, sum(m.score for m in matches) / len(matches))
    except Exception as exc:  # noqa: BLE001 — Pinecone failures must not break the batch
        log.warning(
            "rag_intent_failed",
            contact_id=contact_id,
            error=str(exc),
            error_class=type(exc).__name__,
            exc_info=True,
        )
        _RagBreaker.consecutive_failures += 1
        BREAKER.record_failure("pinecone")  # feed the shared breaker
        if _RagBreaker.consecutive_failures >= _RAG_FAILURE_THRESHOLD:
            _RagBreaker.open = True
            log.error(
                "rag_intent_breaker_open",
                threshold=_RAG_FAILURE_THRESHOLD,
                action="suspending_pinecone_calls_for_rest_of_batch",
            )
        return 0.0


def _engagement_recency(contact: dict) -> float:
    dt = parse_iso_utc(contact.get("last_touch_at"))
    if dt is None:
        return 0.0
    days = (datetime.now(timezone.utc) - dt).days
    return max(0.0, 1.0 - min(days / 30.0, 1.0))


def _engagement_volume(contact: dict) -> float:
    resp = (
        supabase.table("contact_activity_log")
        .select("activity_id", count="exact")
        .eq("contact_id", contact["contact_id"])
        .gte("occurred_at", _ago_iso(30))
        .execute()
    )
    count = resp.count or 0
    return min(count / 10.0, 1.0)


def final_score(fit: float, intent: float, timing: float, persona: str = "default") -> float:
    """Per-archetype Fit/Intent/Timing blend."""
    cfg = settings.agent("icp_scoring")["final_weights"]
    archetype = _archetype_for(persona) if persona != "default" else "default"
    weights = cfg.get(archetype) or cfg.get("default") or {"fit": 0.4, "intent": 0.35, "timing": 0.25}
    return fit * weights["fit"] + intent * weights["intent"] + timing * weights["timing"]


def segment_of(score: float) -> str:
    cfg = settings.agent("icp_scoring")["score_segments"]
    if score >= cfg["hot"]:
        return "hot"
    if score >= cfg["warm"]:
        return "warm"
    if score >= cfg["watchlist"]:
        return "watchlist"
    return "cold"


def explain_intent(router: ClaudeRouter, contact: dict, signals: str) -> str:
    account = contact.get("accounts") or {}
    persona = contact.get("persona", "unknown")
    persona_cfg = settings.shared("personas").get(persona) or {}
    resp = router.complete(
        system=INTENT_EXPLANATION_SYSTEM,
        prompt=INTENT_EXPLANATION_USER.format(
            full_name=contact.get("full_name", ""),
            title=contact.get("title", ""),
            account_name=account.get("name", ""),
            persona=persona,
            persona_tier=persona_cfg.get("tier", 3),
            signals=signals,
        ),
        tier=Tier.FAST,
        max_tokens=80,
    )
    return (resp["text"] or "").strip()


def write_scores(run_date: date, scored: list[ScoredContact]) -> int:
    final_weights_cfg = settings.agent("icp_scoring").get("final_weights", {})
    rows = [{
        "contact_id": str(s.contact_id),
        "run_date": run_date.isoformat(),
        "fit_score": round(s.fit, 4),
        "intent_score": round(s.intent, 4),
        "timing_score": round(s.timing, 4),
        "final_score": round(s.final, 4),
        "segment": s.segment,
        "rationale": s.explanations,
        "weights_snapshot": final_weights_cfg.get(_archetype_for(s.persona) if s.persona else "default")
                             or final_weights_cfg.get("default")
                             or {"fit": 0.4, "intent": 0.35, "timing": 0.25},
    } for s in scored]
    if not rows:
        return 0
    supabase.table("scores").upsert(rows, on_conflict="contact_id,run_date").execute()
    return len(rows)


def render_digest(router: ClaudeRouter, run_date: date, shortlist: list[ScoredContact]) -> str:
    if not shortlist:
        return f"# Omerion Daily Digest — {run_date.isoformat()}\n\nNo new signals."
    import json
    resp = router.complete(
        system=DIGEST_SYSTEM,
        prompt=DIGEST_USER.format(
            run_date=run_date.isoformat(),
            shortlist_json=json.dumps([s.model_dump(mode="json") for s in shortlist], default=str)[:6000],
        ),
        tier=Tier.DEFAULT,
        max_tokens=800,
    )
    return resp["text"]
