"""Tools for Meeting Intelligence (RE discovery → Consulting Proposal)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from uuid import UUID

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.embeddings import embed, embed_batch
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.personas import archetype_for
from omerion_core.settings import settings

from .prompts import (
    BACKLOG_SYSTEM,
    BACKLOG_USER,
    HITL_FLAG_SYSTEM,
    HITL_FLAG_USER,
    PERSONA_CLASSIFY_SYSTEM,
    PROPOSAL_SYSTEM,
    PROPOSAL_USER,
    TTWA_SYSTEM,
    W5H_SYSTEM,
    W5H_USER,
)
from .state import (
    TTWA,
    W5H,
    BacklogItem,
    BlueprintDraft,
    ConsultingProposal,
    PersonaClassification,
    PricingBand,
)

log = get_logger("omerion.agents.meeting_intelligence")


async def fetch_transcript(meeting_id: str) -> dict:
    from omerion_core.clients.fireflies_client import fireflies_client
    payload = await fireflies_client().transcript(meeting_id)
    sentences = payload.get("sentences", []) or []
    text = "\n".join(f"{s.get('speaker_name','?')}: {s.get('text','')}" for s in sentences)
    return {
        "text": text,
        "sentences": sentences,
        "summary_raw": (payload.get("summary") or {}).get("overview", "") or "",
    }


def _parse_json(raw: str, fallback):
    try:
        first = raw.find("{")
        first_arr = raw.find("[")
        if first_arr != -1 and (first == -1 or first_arr < first):
            cut = raw[first_arr : raw.rfind("]") + 1]
            return json.loads(cut)
        cut = raw[first : raw.rfind("}") + 1]
        return json.loads(cut)
    except Exception as exc:  # noqa: BLE001 — LLM output is unpredictable; fallback is intentional
        log.warning(
            "meeting_intel_json_parse_failed",
            error=str(exc),
            error_class=type(exc).__name__,
            raw_preview=(raw or "")[:200],
        )
        return fallback


def extract_w5h(router: ClaudeRouter, transcript: str) -> W5H:
    resp = router.complete(
        system=W5H_SYSTEM,
        prompt=W5H_USER.format(transcript=transcript[:24000]),
        tier=Tier.HEAVY,
        max_tokens=1200,
    )
    raw_text = resp["text"] or ""
    data = _parse_json(raw_text, None)
    if not isinstance(data, dict):
        # Fail loud rather than silently returning an empty W5H, which would be
        # indistinguishable from a legitimately sparse meeting and corrupt every
        # downstream blueprint.
        raise ValueError(
            f"extract_w5h could not parse JSON from LLM response (len={len(raw_text)})"
        )
    return W5H(
        who=data.get("who") or [],
        what=data.get("what", "") or "",
        where=data.get("where", "") or "",
        when=data.get("when", "") or "",
        how_much=data.get("how_much", "") or "",
    )


def extract_ttwa(router: ClaudeRouter, w5h: W5H, transcript: str) -> TTWA:
    resp = router.complete(
        system=TTWA_SYSTEM,
        prompt=f"W5H: {w5h.model_dump_json()}\nTranscript excerpt:\n{transcript[:8000]}",
        tier=Tier.HEAVY,
        max_tokens=500,
    )
    raw_text = resp["text"] or ""
    data = _parse_json(raw_text, None)
    if not isinstance(data, dict) or not data:
        raise ValueError(
            f"extract_ttwa could not parse JSON from LLM response (len={len(raw_text)})"
        )
    return TTWA(**data)


def classify_persona(router: ClaudeRouter, w5h: W5H, transcript: str) -> PersonaClassification:
    resp = router.complete(
        system=PERSONA_CLASSIFY_SYSTEM,
        prompt=f"W5H: {w5h.model_dump_json()}\nTranscript excerpt:\n{transcript[:6000]}",
        tier=Tier.DEFAULT,
        max_tokens=200,
    )
    data = _parse_json(resp["text"], {})
    personas = settings.shared("personas")
    persona = data.get("persona") or "unknown"
    if persona not in personas:
        persona = "unknown"
    # Why explicit None check: prior `data.get("persona_tier") or ...` treated a
    # legitimate tier=0 as missing, causing the fallback to overwrite valid LLM output.
    raw_tier = data.get("persona_tier")
    if raw_tier is None:
        raw_tier = personas.get(persona, {}).get("tier")
    tier = int(raw_tier) if raw_tier is not None else 3
    return PersonaClassification(
        persona=persona,
        persona_tier=tier,
        archetype=archetype_for(persona),
        confidence=float(data.get("confidence", 0.5) or 0.5),
    )


def _offer_packages() -> dict:
    return settings.shared("offer_packages") or {}


def _demo_catalog() -> dict:
    return settings.shared("demo_catalog") or {}


def synthesize_proposal(
    router: ClaudeRouter,
    persona: str,
    persona_tier: int,
    w5h: W5H,
    ttwa: TTWA,
    constraints: dict,
    archetype: str = "system_multiplier",
    past_context_snippets: list[str] | None = None,
) -> ConsultingProposal:
    past_context_block = (
        "Prior meetings with this account:\n" + "\n---\n".join(past_context_snippets)
        if past_context_snippets
        else "(no prior meeting context)"
    )
    resp = router.complete(
        system=PROPOSAL_SYSTEM,
        prompt=PROPOSAL_USER.format(
            archetype=archetype,
            persona=persona,
            persona_tier=persona_tier,
            w5h_json=w5h.model_dump_json(),
            ttwa_json=ttwa.model_dump_json(),
            constraints_json=json.dumps(constraints),
            offer_packages_json=json.dumps(_offer_packages()),
            demo_catalog_json=json.dumps(_demo_catalog()),
            past_context_block=past_context_block,
        ),
        tier=Tier.HEAVY,
        max_tokens=2500,
    )
    data = _parse_json(resp["text"], {})
    pricing_raw = data.get("pricing") or {}
    band = pricing_raw.get("band") or [0, 0]
    pricing = PricingBand(
        price_usd=float(pricing_raw.get("price_usd") or 0.0),
        band=(int(band[0]) if band else 0, int(band[1]) if len(band) > 1 else 0),
        rationale=pricing_raw.get("rationale", "") or "",
    )
    return ConsultingProposal(
        exec_summary=data.get("exec_summary", "") or "",
        problem_statement_w5h=data.get("problem_statement_w5h", "") or "",
        operator_archetype=archetype or None,
        recommended_service_package=data.get("recommended_service_package"),
        demo_reference=data.get("demo_reference"),
        demo_plan=data.get("demo_plan", "") or "",
        thirty_sixty_ninety=data.get("thirty_sixty_ninety") or {},
        pricing=pricing,
        success_metrics=data.get("success_metrics") or [],
        next_steps=data.get("next_steps") or [],
    )


def build_backlog(
    router: ClaudeRouter,
    proposal: ConsultingProposal,
    constraints: dict,
) -> list[BacklogItem]:
    resp = router.complete(
        system=BACKLOG_SYSTEM.format(
            constraints_json=json.dumps(constraints),
            service_package=proposal.recommended_service_package or "unknown",
            demo_reference=proposal.demo_reference or "unknown",
        ),
        prompt=BACKLOG_USER.format(proposal_json=proposal.model_dump_json()),
        tier=Tier.HEAVY,
        max_tokens=2000,
    )
    items = _parse_json(resp["text"], [])
    out: list[BacklogItem] = []
    for it in items if isinstance(items, list) else []:
        try:
            out.append(BacklogItem(**it))
        except Exception:  # noqa: BLE001
            continue
    return out


def raise_flags(router: ClaudeRouter, draft: BlueprintDraft) -> tuple[list[str], float]:
    allowed = set(settings.agent("meeting_intelligence")["hitl_flag_conditions"])
    resp = router.complete(
        system=HITL_FLAG_SYSTEM,
        prompt=HITL_FLAG_USER.format(draft_json=draft.model_dump_json()),
        tier=Tier.DEFAULT,
        max_tokens=300,
    )
    parsed = _parse_json(resp["text"], {"flags": [], "confidence": 0.5})
    flags = [f for f in parsed.get("flags", []) if f in allowed]
    confidence = float(parsed.get("confidence", 0.5) or 0.5)
    return flags, confidence


def persist_blueprint(draft: BlueprintDraft, meeting_id: str, correlation_id) -> UUID:
    """Wave 2.4: hitl_flags is now schema-validated before the insert.

    The legacy shape (`list[str]`) is accepted and coerced to typed
    HitlFlag rows for backwards-compat. New code should emit the
    structured shape. Malformed payloads raise pydantic ValidationError,
    which the calling node catches and routes to HITL with the bad
    payload attached.

    `idempotency_key` deduplicates blueprint inserts on
    (meeting_id, day) so a retried meeting-intelligence run can't
    produce two blueprints for the same transcript.
    """
    from omerion_core.util.idempotency import generate_key

    from .contracts import validate_hitl_flags

    # Wave 2.4: validate hitl_flags structure. Raises on malformed input.
    validated_flags = validate_hitl_flags(draft.hitl_flags)

    idempotency_key = generate_key(
        scope="blueprint.meeting_intelligence",
        payload={"meeting_id": meeting_id},
        window="day",
    )

    row = {
        "account_id": str(draft.account_id) if draft.account_id else None,
        "contact_id": str(draft.contact_id) if draft.contact_id else None,
        "meeting_id": meeting_id,
        "persona": draft.persona,
        "persona_tier": draft.persona_tier,
        "w5h": draft.w5h.model_dump(),
        "ttwa": draft.ttwa.model_dump(),
        "proposal": draft.proposal.model_dump(),
        "proposal_schema_version": settings.agent("meeting_intelligence").get("proposal_schema_version", "consulting_v1"),
        "constraints": draft.constraints,
        "backlog": [b.model_dump() for b in draft.backlog],
        "hitl_flags": validated_flags.to_jsonb(),  # Typed shape, not freeform
        "hitl_requires_review": validated_flags.requires_review,
        "confidence": draft.confidence,
        "status": "draft",
        "correlation_id": str(correlation_id) if correlation_id else None,
        "idempotency_key": idempotency_key,
    }
    resp = (
        supabase.table("blueprints")
        .upsert(row, on_conflict="idempotency_key", ignore_duplicates=True)
        .execute()
    )
    if not resp.data:
        # Upsert was a no-op — this meeting already has a blueprint for today.
        # Fetch the existing blueprint_id so the graph can continue idempotently.
        existing = (
            supabase.table("blueprints")
            .select("blueprint_id")
            .eq("idempotency_key", idempotency_key)
            .limit(1)
            .execute()
        )
        if existing.data:
            return UUID(existing.data[0]["blueprint_id"])
        raise RuntimeError(
            f"persist_blueprint: upsert was no-op but no existing row found "
            f"for idempotency_key={idempotency_key}"
        )
    return UUID(resp.data[0]["blueprint_id"])


def query_past_context(account_id: str | None, w5h_what: str) -> list[str]:
    """Retrieve similar past meeting snippets from Pinecone transcripts namespace.

    Returns up to 3 text chunks (≤500 chars each). Returns [] when the namespace
    is empty, account_id is None, or Pinecone is unavailable — always safe to call.
    Score threshold 0.70 filters out low-relevance cold-start noise once the
    namespace begins to populate (typically after 20+ meetings for the account).
    """
    if not account_id or not w5h_what.strip():
        return []
    try:
        vector = embed(w5h_what[:500])
        idx = pinecone_index()
        result = idx.query(
            vector=vector,
            top_k=3,
            include_metadata=True,
            filter={"account_id": {"$eq": account_id}},
            namespace="transcripts",
        )
        snippets = []
        for match in result.matches:
            if match.score > 0.70 and match.metadata.get("text"):
                snippets.append(match.metadata["text"][:500])
        return snippets
    except Exception as exc:
        log.warning("meeting_intel_rag_query_failed", error=str(exc))
        return []


_CURRENT_SCHEMA_VERSION = "consulting_v1"


def coerce_blueprint_schema(row: dict) -> dict:
    """Upgrade a blueprint DB row to the current proposal schema version.

    Called by any consumer that reads from the blueprints table (e.g.,
    build_orchestrator after BLUEPRINT_APPROVED). For now this is a no-op —
    consulting_v1 is the only version. When consulting_v2 is introduced, add
    the migration branch here:

        if row.get("proposal_schema_version") == "consulting_v1":
            row["proposal"] = _migrate_v1_to_v2(row["proposal"])
            row["proposal_schema_version"] = "consulting_v2"
    """
    version = row.get("proposal_schema_version", _CURRENT_SCHEMA_VERSION)
    if version == _CURRENT_SCHEMA_VERSION:
        return row
    log.warning(
        "blueprint_schema_version_unknown",
        version=version,
        blueprint_id=row.get("blueprint_id"),
    )
    return row


_TRANSCRIPTS_NS = "transcripts"
_DEDUP_HARD = 0.96
_DEDUP_SOFT = 0.90


def chunk_and_embed_transcript(meeting_id: str, transcript: str, metadata: dict) -> int:
    """Embed transcript chunks into Pinecone `transcripts` namespace.

    Enforces mandatory metadata schema regardless of caller-supplied fields.
    Applies dual-threshold dedup before every upsert:
      cosine >= 0.96 → hard skip (silent drop)
      cosine 0.90-0.95 → soft flag (insert with is_apparent_duplicate=True)
      cosine < 0.90 → clean write

    Returns count of vectors upserted (skipped chunks are not counted).
    """
    chunks = _chunk_text(transcript, target=900)
    if not chunks:
        return 0

    vectors = embed_batch(chunks)
    idx = pinecone_index()
    run_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # Build mandatory metadata — overrides any caller-supplied conflicting keys.
    # Caller may supply account_id, contact_id, source_url — preserved as agent-specific fields.
    base_meta = {
        **{k: v for k, v in metadata.items() if k not in ("agent_type", "agent_id", "department", "namespace", "run_date", "chunk", "chunk_index")},
        "agent_id": "meeting_intelligence",   # mandatory — replaces legacy agent_type
        "department": "delivery",             # mandatory
        "namespace": _TRANSCRIPTS_NS,         # mandatory
        "run_date": run_date,                 # mandatory
        "meeting_id": meeting_id,
    }

    # Batch dedup: query one representative vector per chunk against existing namespace.
    # We query each chunk individually (top_k=1) to get the closest existing match.
    upserts: list[dict] = []
    skipped_hard = 0
    flagged_soft = 0

    for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
        chunk_meta = {**base_meta, "chunk_index": i, "text": chunk[:900]}
        vector_id = f"transcript:{meeting_id}:{i}"

        try:
            result = idx.query(
                vector=vec,
                top_k=1,
                include_metadata=False,
                filter={"agent_id": {"$eq": "meeting_intelligence"}, "meeting_id": {"$eq": meeting_id}},
                namespace=_TRANSCRIPTS_NS,
            )
            if result.matches:
                score = result.matches[0].score
                if score >= _DEDUP_HARD:
                    skipped_hard += 1
                    continue
                if score >= _DEDUP_SOFT:
                    chunk_meta["is_apparent_duplicate"] = True
                    flagged_soft += 1
        except Exception as exc:
            log.warning("meeting_intel_dedup_query_failed", chunk_index=i, error=str(exc))
            # Proceed without dedup on query failure — do not block embedding.

        upserts.append({"id": vector_id, "values": vec, "metadata": chunk_meta})

    if upserts:
        idx.upsert(vectors=upserts, namespace=_TRANSCRIPTS_NS)

    log.info(
        "transcript_chunks_embedded",
        meeting_id=meeting_id,
        upserted=len(upserts),
        skipped_hard=skipped_hard,
        flagged_soft=flagged_soft,
    )
    return len(upserts)


def _chunk_text(text: str, target: int = 900) -> list[str]:
    if not text:
        return []
    words = text.split()
    chunks: list[str] = []
    cur: list[str] = []
    count = 0
    for w in words:
        cur.append(w)
        count += len(w) + 1
        if count >= target:
            chunks.append(" ".join(cur))
            cur, count = [], 0
    if cur:
        chunks.append(" ".join(cur))
    return chunks
