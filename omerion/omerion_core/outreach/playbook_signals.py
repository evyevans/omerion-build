"""Playbook signals — Pinecone write helpers for the `playbooks` namespace.

offer_matching (Agent #7) reads from this namespace in find_similar_wins()
to retrieve historical proposal patterns as RAG context for Opus synthesis.
This module is the missing write side.

Namespace: playbooks (dim=1536, cosine, text-embedding-3-small)

Each vector represents one founder-approved proposal. Written in offer_matching's
emit_node after an opportunity row is successfully created. Confidence threshold:
only index proposals with confidence >= 0.70 to avoid polluting future context
with weak-match patterns.
"""
from __future__ import annotations

import asyncio
from datetime import date


def _today_iso() -> str:
    return date.today().isoformat()


async def write_playbook_signal(
    *,
    opportunity_id: str,
    contact_id: str,
    persona: str,
    service_package: str,
    demo_reference: str,
    value_bucket: str | None,
    rationale: str,
    memo_preview: str,          # first 300 chars of memo_md
    pain_signals: list[str],
    confidence: float,
    agent_name: str,
) -> None:
    """Upsert one approved proposal vector into Pinecone `playbooks` namespace.

    Skips indexing if confidence < 0.70 — low-confidence proposals are noise.

    Applies dual-threshold dedup on write:
    - cosine >= 0.96 → hard skip
    - cosine 0.90–0.95 → insert with is_apparent_duplicate=true
    - cosine < 0.90 → clean write

    Vector ID is deterministic on opportunity_id so retries are idempotent.
    """
    if confidence < 0.70:
        return

    from omerion_core.clients.pinecone_client import get_async_index
    from omerion_core.llm.embeddings import embed
    from omerion_core.logging import get_logger

    log = get_logger("omerion.outreach.playbook_signals")

    idx = get_async_index()
    if idx is None:
        return

    pain_str = " ".join(pain_signals[:5])
    text = (
        f"persona:{persona} package:{service_package} demo:{demo_reference} "
        f"pain:{pain_str} rationale:{rationale[:200]} memo:{memo_preview}"
    )
    vector_id = f"opp:{opportunity_id}"

    try:
        vector = await asyncio.to_thread(embed, text)

        existing = await idx.query(
            vector=vector,
            namespace="playbooks",
            top_k=5,
            filter={"persona": {"$eq": persona}},
            include_metadata=True,
        )
        is_apparent_dup = False
        if existing.matches:
            best = existing.matches[0].score
            if best >= 0.96:
                log.info("playbook_signal_hard_dedup_skip", vector_id=vector_id, score=best)
                return
            elif best >= 0.90:
                is_apparent_dup = True
                log.info("playbook_signal_soft_dedup_flag", vector_id=vector_id, score=best)

        metadata: dict = {
            "agent_id": agent_name,
            "department": "revenue",
            "namespace": "playbooks",
            "run_date": _today_iso(),
            "persona": persona,
            "service_package": service_package,
            "demo_reference": demo_reference,
            "value_bucket": value_bucket or "unknown",
            "confidence": round(confidence, 4),
            "opportunity_id": opportunity_id,
            "contact_id": contact_id,
        }
        if is_apparent_dup:
            metadata["is_apparent_duplicate"] = True

        await idx.upsert(
            vectors=[{"id": vector_id, "values": vector, "metadata": metadata}],
            namespace="playbooks",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("playbook_signal_write_error", vector_id=vector_id, error=str(exc))
