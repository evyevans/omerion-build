"""Tools for Offer Matching & Playbook (Agent #7) — RE consulting pivot.

The agent produces one `service_package` + `demo_reference` per hot contact;
`opportunities` rows carry those fields instead of the old module/tier pair.
"""
from __future__ import annotations

import json
import statistics
from typing import Iterable, get_args
from uuid import UUID

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.embeddings import embed
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier as LLMTier
from omerion_core.logging import get_logger
from omerion_core.settings import settings

from .prompts import OFFER_SYSTEM, OFFER_USER
from .state import DemoReference, OfferProposal, PlaybookPhase, ServicePackage

log = get_logger("omerion.agents.offer_matching")

_VALID_PACKAGES: set[str] = set(get_args(ServicePackage))
_VALID_DEMOS: set[str] = set(get_args(DemoReference))


def _offer_packages() -> dict:
    return settings.shared("offer_packages")


def _demo_catalog() -> dict:
    return settings.shared("demo_catalog")


def _personas() -> dict:
    return settings.shared("personas")


def load_hot_contacts(contact_ids: Iterable[UUID] | None = None, limit: int = 25) -> list[dict]:
    """Pull contacts that are 'hot' per the latest score row."""
    q = supabase.table("scores").select(
        "contact_id,run_date,fit_score,intent_score,timing_score,final_score,segment,rationale,"
        "contacts(contact_id,first_name,last_name,role,persona,account_id,"
        "accounts(name,market,pain_signal))"
    ).eq("segment", "hot").order("run_date", desc=True)
    if contact_ids:
        q = q.in_("contact_id", [str(c) for c in contact_ids])
    rows = q.limit(limit).execute().data or []
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in rows:
        cid = r.get("contact_id")
        if cid in seen:
            continue
        seen.add(cid)
        deduped.append(r)
    return deduped


def _strongest_pain(contact_row: dict) -> list[str]:
    rationale = contact_row.get("rationale") or {}
    explanation = rationale.get("why_now") if isinstance(rationale, dict) else None
    if explanation:
        return [explanation]
    account = (contact_row.get("contacts") or {}).get("accounts") or {}
    return [account.get("pain_signal")] if account.get("pain_signal") else []


def find_similar_wins(persona: str, pain: list[str]) -> list[str]:
    """RAG over the `playbooks` namespace for the closest historical wins."""
    cfg = settings.agent("offer_matching")
    threshold = float(cfg.get("rag_similarity_threshold", 0.78))
    if not pain:
        return []
    try:
        vector = embed(" ".join(pain)[:1500])
        results = pinecone_index().query(
            vector=vector, top_k=3, namespace="playbooks",  # Policy cap: max 3
            filter={"persona": {"$eq": persona}},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("offer_rag_failed", error=str(exc))
        return []
    return [m.id for m in results.matches if m.score >= threshold]


def _clip_confidence(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def _price_band_for(package: str) -> dict:
    entry = (_offer_packages().get(package) or {}).get("price_band") or {}
    if not entry:
        return {}
    return {"min": float(entry.get("min", 0)), "max": float(entry.get("max", 0)), "currency": "USD"}


def _midpoint(band: dict) -> float:
    if not band:
        return 0.0
    return (float(band.get("min", 0)) + float(band.get("max", 0))) / 2.0


def _bucket_for_value(value_usd: float) -> str | None:
    """Deterministically map a dollar amount to an S/M/L/XL bucket.

    Wave 2.2: an LLM never picks the bucket directly; the bucket is derived
    from the deterministic price-band midpoint. This function is the single
    source of truth for that mapping — `settings.value_bucket_ranges_usd`
    provides the (low, mid, high) tuples per bucket.

    A value above the largest bucket's high bound returns "XL" (the wrapper
    then enforces the MAX_OPPORTUNITY_VALUE_USD cap and routes to HITL).
    """
    from omerion_core.settings import settings

    if value_usd <= 0:
        return None
    # Buckets are sorted from smallest to largest by their high bound.
    ranges = settings.value_bucket_ranges_usd
    sorted_buckets = sorted(ranges.items(), key=lambda kv: kv[1][2])
    for label, (_low, _mid, high) in sorted_buckets:
        if value_usd <= high:
            return label
    return sorted_buckets[-1][0]  # above max → top bucket (wrapper caps)


def _persona_tier(persona: str) -> int:
    return int((_personas().get(persona) or {}).get("tier", 1))


def _default_demo_for_package(package: str) -> str | None:
    demo = (_offer_packages().get(package) or {}).get("demo_reference")
    return demo if demo in _VALID_DEMOS else None


def _validate_package_demo_pair(package: str | None, demo: str | None) -> str | None:
    """Return a demo_reference that is valid for the given package, or fall back.

    Why: prior to this, synthesize_proposal trusted whatever (package, demo) pair
    the LLM emitted. Two failure modes that mattered:
      1. LLM picks a real package and a real demo that don't actually pair in
         the catalog (e.g. RORA + a DAAM demo). Downstream proposal renders
         contradictory copy.
      2. LLM picks a package but no demo. We already had a fallback for that,
         but it didn't run when demo was set-but-wrong.
    Now both cases route through the catalog default.
    """
    if not package:
        return demo if demo in _VALID_DEMOS else None
    catalog_demo = _default_demo_for_package(package)
    if demo in _VALID_DEMOS and demo == catalog_demo:
        return demo
    return catalog_demo


def synthesize_proposal(
    router: ClaudeRouter,
    contact_row: dict,
    cached_similar: list[str] | None = None,
) -> OfferProposal:
    contact = contact_row.get("contacts") or {}
    account = contact.get("accounts") or {}
    persona = contact.get("persona") or "unknown"
    pain = _strongest_pain(contact_row)
    # Use pre-computed cache from state if provided; otherwise query Pinecone.
    similar = cached_similar if cached_similar is not None else find_similar_wins(persona, pain)

    packages_json = json.dumps(_offer_packages())[:4000]
    demos_json = json.dumps(_demo_catalog())[:2000]

    resp = router.complete(
        system=OFFER_SYSTEM,
        prompt=OFFER_USER.format(
            first_name=contact.get("first_name", ""),
            last_name=contact.get("last_name", ""),
            title=contact.get("role", ""),
            account_name=account.get("name", ""),
            market=account.get("market", ""),
            persona=persona,
            persona_tier=_persona_tier(persona),
            final_score=round(float(contact_row.get("final_score") or 0), 4),
            segment=contact_row.get("segment", "hot"),
            pain_signals="\n".join(f"- {p}" for p in pain) or "- (none surfaced)",
            similar_json=json.dumps(similar)[:1500],
            offer_packages_json=packages_json,
            demo_catalog_json=demos_json,
        ),
        tier=LLMTier.HEAVY,
        max_tokens=1800,
        temperature=0.3,
    )
    data, _ok = extract_json_object(resp["text"])

    pkg_raw = str(data.get("service_package", "") or "")
    service_package: ServicePackage | None = pkg_raw if pkg_raw in _VALID_PACKAGES else None  # type: ignore[assignment]

    demo_raw = str(data.get("demo_reference", "") or "")
    demo_reference: DemoReference | None = _validate_package_demo_pair(  # type: ignore[assignment]
        service_package, demo_raw or None,
    )

    band = _price_band_for(service_package) if service_package else {}

    playbook: list[PlaybookPhase] = []
    for entry in (data.get("playbook") or []):
        label = str(entry.get("label", ""))
        if label not in {"30", "60", "90"}:
            continue
        playbook.append(PlaybookPhase(
            label=label,  # type: ignore[arg-type]
            objective=str(entry.get("objective", "")),
            deliverables=[str(x) for x in (entry.get("deliverables") or [])][:6],
            success_metrics=[str(x) for x in (entry.get("success_metrics") or [])][:6],
        ))

    deterministic_value_usd = _midpoint(band)
    value_bucket = _bucket_for_value(deterministic_value_usd)

    return OfferProposal(
        contact_id=UUID(contact["contact_id"]),
        account_id=UUID(contact["account_id"]) if contact.get("account_id") else None,
        persona=persona,
        persona_tier=_persona_tier(persona),
        service_package=service_package,
        demo_reference=demo_reference,
        price_band=band,
        # Wave 2.2: value_bucket is deterministic — LLM never picks the dollars.
        value_bucket=value_bucket,  # type: ignore[arg-type]
        value_est_usd=deterministic_value_usd,
        rationale=str(data.get("rationale", "")).strip(),
        playbook=playbook,
        memo_md=str(data.get("memo_md", "")).strip(),
        confidence=_clip_confidence(data.get("confidence", 0.0)),
        similar_account_ids=similar,
    )


def write_opportunity(proposal: OfferProposal) -> UUID | None:
    """Persist an opportunity row.

    Wave 2.1/2.2 invariants:
      * `value_est_usd` is deterministic — derived from the price_band
        midpoint via `_midpoint()`, not from any LLM token.
      * `value_bucket` is stamped so downstream consumers (dashboards,
        attribution) work with the discrete bucket label rather than a
        raw dollar.
      * `idempotency_key` is the offer_matching natural key
        (contact_id + service_package + day-bucket) — duplicate
        proposals for the same contact/package/day are no-ops at the
        DB layer via the UNIQUE index from migration 0040.
      * The MAX_OPPORTUNITY_VALUE_USD cap is enforced *outside* this
        function (by the wrapper's value-bound check). This function is
        the trusted write site once the wrapper has approved.
    """
    if not proposal.service_package:
        return None

    from omerion_core.util.idempotency import generate_key

    idempotency_key = generate_key(
        scope="opportunity.offer_matching",
        payload={
            "contact_id": str(proposal.contact_id),
            "service_package": proposal.service_package,
        },
        window="day",
    )

    row = {
        "contact_id": str(proposal.contact_id),
        "account_id": str(proposal.account_id) if proposal.account_id else None,
        "stage": "engaged",
        "offer_modules": proposal.service_package,
        "value_est_usd": round(proposal.value_est_usd, 2),
        "pricing_band": proposal.price_band,
        "idempotency_key": idempotency_key,
        "metadata": {
            "persona": proposal.persona,
            "persona_tier": proposal.persona_tier,
            "rationale": proposal.rationale,
            "demo_reference": proposal.demo_reference,
            "playbook": [p.model_dump() for p in proposal.playbook],
            "confidence": proposal.confidence,
            "similar_account_ids": proposal.similar_account_ids,
            "value_bucket": proposal.value_bucket,
            "value_source": "deterministic_midpoint",  # Wave 2.2 audit marker
        },
    }
    # UPSERT (not INSERT) on the stamped idempotency_key. The partial UNIQUE
    # index `opportunities_idempotency_uidx` (migration 0040) means a plain
    # INSERT of a duplicate (same contact+service_package+day) raises a
    # unique-violation — which would crash persist_node *after* the founder
    # already approved, leaving a half-written batch and a consumed approval.
    # ignore_duplicates makes a re-run a clean no-op: the first proposal +
    # memo stand, and we return None so write_memo_draft is skipped and
    # opportunities_created is not double-counted.
    resp = (
        supabase.table("opportunities")
        .upsert(row, on_conflict="idempotency_key", ignore_duplicates=True)
        .execute()
    )
    rows = resp.data or []
    return UUID(rows[0]["opportunity_id"]) if rows else None


def write_memo_draft(proposal: OfferProposal, opportunity_id: UUID | None) -> UUID | None:
    if not proposal.memo_md:
        return None
    if opportunity_id is None:
        # Don't write orphaned drafts: a memo only makes sense once an
        # opportunity row exists to attach it to. Caller should write_opportunity
        # first; if that returned None the caller skips this draft entirely.
        return None
    resp = supabase.table("generated_drafts").insert({
        "agent_name": "offer_matching",
        "contact_id": str(proposal.contact_id),
        "opportunity_id": str(opportunity_id),
        "purpose": "offer_memo",
        "model": "claude-opus",
        "draft_body": proposal.memo_md,
        "draft_metadata": {
            "service_package": proposal.service_package,
            "demo_reference": proposal.demo_reference,
            "persona": proposal.persona,
            "persona_tier": proposal.persona_tier,
            "value_est_usd": round(proposal.value_est_usd, 2),
            "confidence": proposal.confidence,
        },
    }).execute()
    rows = resp.data or []
    return UUID(rows[0]["draft_id"]) if rows else None


def summary_stats(proposals: list[OfferProposal]) -> dict:
    if not proposals:
        return {"count": 0, "avg_value": 0.0, "packages": {}}
    values = [p.value_est_usd for p in proposals if p.value_est_usd > 0]
    packages: dict[str, int] = {}
    for p in proposals:
        key = p.service_package or "unassigned"
        packages[key] = packages.get(key, 0) + 1
    return {
        "count": len(proposals),
        "avg_value": round(statistics.mean(values), 2) if values else 0.0,
        "packages": packages,
    }
