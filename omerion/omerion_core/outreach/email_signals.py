"""Email signals — Pinecone write helpers for the `emails` namespace.

icp_scoring (Agent #6) reads from this namespace to compute `semantic_pain_match`
in the Intent sub-score. This module is the missing write side.

Namespace: emails (dim=1536, cosine, text-embedding-3-small)

Each vector represents one email interaction event (sent / replied / opened / clicked).
The icp_scoring RAG query fires per-contact with a filter on `contact_id`, so the
partition boundary is the contact — one contact accumulates multiple event vectors
over time, and the average cosine score against the archetype query is the intent signal.

Call write_email_signal() from:
  - crm_nurture's send_or_discard node (event_type="sent") after deliver() succeeds
  - A Gmail reply-detection poller (event_type="replied") when a thread reply is seen
  - Any future email open/click tracking webhook (event_type="opened"/"clicked")
"""
from __future__ import annotations

import asyncio
from datetime import date


def _today_iso() -> str:
    return date.today().isoformat()


async def write_email_signal(
    *,
    contact_id: str,
    event_type: str,                # "sent" | "replied" | "opened" | "clicked"
    subject: str,
    body_preview: str,              # first 300 chars of email body
    template_key: str,
    persona: str,
    stage: str,
    provider_id: str,               # Gmail message_id — idempotency anchor
    agent_name: str,
) -> None:
    """Upsert one email interaction vector into Pinecone `emails` namespace.

    Applies dual-threshold dedup on write:
    - cosine >= 0.96 → hard skip
    - cosine 0.90–0.95 → insert with is_apparent_duplicate=true
    - cosine < 0.90 → clean write

    Vector ID is deterministic: contact_id + provider_id + event_type so a retry
    on the same Gmail message never produces duplicate vectors.
    """
    import asyncio
    from omerion_core.clients.pinecone_client import get_async_index
    from omerion_core.llm.embeddings import embed
    from omerion_core.logging import get_logger

    log = get_logger("omerion.outreach.email_signals")

    idx = get_async_index()
    if idx is None:
        return

    text = (
        f"event:{event_type} persona:{persona} stage:{stage} "
        f"template:{template_key} subject:{subject} "
        f"body:{body_preview[:300]}"
    )
    vector_id = f"{contact_id}:{provider_id}:{event_type}"

    try:
        vector = await asyncio.to_thread(embed, text)

        existing = await idx.query(
            vector=vector,
            namespace="emails",
            top_k=5,
            filter={"contact_id": {"$eq": contact_id}},
            include_metadata=True,
        )
        is_apparent_dup = False
        if existing.matches:
            best = existing.matches[0].score
            if best >= 0.96:
                log.info("email_signal_hard_dedup_skip", vector_id=vector_id, score=best)
                return
            elif best >= 0.90:
                is_apparent_dup = True
                log.info("email_signal_soft_dedup_flag", vector_id=vector_id, score=best)

        metadata: dict = {
            "agent_id": agent_name,
            "department": "revenue",
            "namespace": "emails",
            "contact_id": contact_id,
            "run_date": _today_iso(),
            "event_type": event_type,
            "subject": subject[:120],
            "template_key": template_key,
            "persona": persona,
            "stage": stage,
            "body_preview": body_preview[:300],
        }
        if is_apparent_dup:
            metadata["is_apparent_duplicate"] = True

        await idx.upsert(
            vectors=[{"id": vector_id, "values": vector, "metadata": metadata}],
            namespace="emails",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("email_signal_write_error", vector_id=vector_id, error=str(exc))
