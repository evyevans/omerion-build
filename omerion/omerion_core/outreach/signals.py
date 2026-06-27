"""Outreach signals — Pinecone read/write helpers for the RAG data flywheel.

Both REACH (linkedin_outreach) and NURTURE (crm_nurture) use these functions
in their rag_augment and write_signals nodes.

Namespace: outreach_signals (dim=1536, cosine, text-embedding-3-small)

Each vector represents one outreach interaction. Vectors whose reply_received=0
are updated to reply_received=1 by the response tracker when a reply is detected.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any


def _today_iso() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def query_outreach_signals(persona: str, stage: str, top_k: int = 3) -> str:
    """Query Pinecone for successful past outreach patterns for this persona/stage.

    Returns a formatted string ready to inject into a Claude system prompt.
    Returns empty string if Pinecone is unavailable or no signals exist yet.

    Top_k capped at 3 per audit policy. Filters for reply_received=1 only.
    """
    from omerion_core.clients.pinecone_client import get_async_index
    from omerion_core.llm.embeddings import embed
    from omerion_core.logging import get_logger

    log = get_logger("omerion.outreach.signals")

    idx = get_async_index()
    if idx is None:
        return ""

    query_text = f"successful outreach for {persona} persona in {stage} stage with reply received"
    top_k = min(top_k, 3)
    try:
        vector = await asyncio.to_thread(embed, query_text)
        results = await idx.query(
            vector=vector,
            namespace="outreach_signals",
            top_k=top_k,
            filter={"reply_received": {"$eq": 1}},
            include_metadata=True,
        )
        matches = results.matches if hasattr(results, "matches") else []
        if not matches:
            return ""

        lines = []
        for r in matches:
            meta = r.metadata or {}
            template = meta.get("template_key", "unknown")
            channel = meta.get("channel", "unknown")
            angle = meta.get("angle", "unknown")
            days = meta.get("days_to_reply", -1)
            days_str = f"{days}d" if days > 0 else "?"
            lines.append(f"- {template} ({channel}, angle: {angle}): reply in {days_str}")

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach_signals_query_error", persona=persona, stage=stage, error=str(exc))
        return ""


async def write_outreach_signal(
    *,
    persona: str,
    stage: str,
    channel: str,
    template_key: str,
    angle: str,
    reply_received: bool,
    days_to_reply: int,
    contact_id: str,
    agent_name: str,
    run_id: str,
) -> None:
    """Upsert one interaction signal into Pinecone outreach_signals namespace.

    Applies dual-threshold dedup on write:
    - cosine >= 0.96 → hard skip (drop silently)
    - cosine 0.90–0.95 → insert with is_apparent_duplicate=true
    - cosine < 0.90 → clean write
    """
    from omerion_core.clients.pinecone_client import get_async_index
    from omerion_core.llm.embeddings import embed
    from omerion_core.logging import get_logger

    log = get_logger("omerion.outreach.signals")

    idx = get_async_index()
    if idx is None:
        log.warning("outreach_signal_write_skipped_no_index", contact_id=contact_id)
        return

    outcome = "reply" if reply_received else "no_reply"
    text = (
        f"persona:{persona} stage:{stage} channel:{channel} "
        f"angle:{angle} template:{template_key} outcome:{outcome} "
        f"days_to_reply:{days_to_reply}"
    )
    vector_id = f"{contact_id}:{run_id}:{template_key}"
    try:
        vector = await asyncio.to_thread(embed, text)

        existing_results = await idx.query(
            vector=vector,
            namespace="outreach_signals",
            top_k=5,
            include_metadata=True,
        )
        is_apparent_dup = False
        if existing_results.matches:
            best_match_score = existing_results.matches[0].score
            if best_match_score >= 0.96:
                log.info("outreach_signal_hard_dedup_skip", vector_id=vector_id, score=best_match_score)
                return
            elif best_match_score >= 0.90:
                is_apparent_dup = True
                log.info("outreach_signal_soft_dedup_flag", vector_id=vector_id, score=best_match_score)

        metadata = {
            "agent_id": agent_name,
            "department": "revenue",
            "namespace": "outreach_signals",
            "persona": persona,
            "stage": stage,
            "channel": channel,
            "template_key": template_key,
            "angle": angle,
            "reply_received": 1 if reply_received else 0,
            "days_to_reply": days_to_reply,
            "contact_id": contact_id,
            "run_date": _today_iso(),
            "content_date": _today_iso(),
            "persona_meta": persona,
            "market": "general_b2b",
            "source_url": f"internal://{agent_name}",
        }
        if is_apparent_dup:
            metadata["is_apparent_duplicate"] = True

        await idx.upsert(
            vectors=[{
                "id": vector_id,
                "values": vector,
                "metadata": metadata,
            }],
            namespace="outreach_signals",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach_signals_write_error", vector_id=vector_id, error=str(exc))


def upsert_outreach_thread(contact_id: str, channel: str) -> None:
    """Maintain the outreach_threads row for this contact after a send.

    Increments the touch counter for the given channel and updates last_touch_at.
    Uses upsert-on-conflict since one row per contact is enforced by the UNIQUE constraint.
    """
    from omerion_core.clients.supabase_client import supabase
    from omerion_core.logging import get_logger

    log = get_logger("omerion.outreach.signals")

    col_map = {
        "email": "touch_count_email",
        "sms": "touch_count_email",
        "linkedin": "touch_count_linkedin",
        "linkedin_dm": "touch_count_linkedin",
    }
    col = col_map.get(channel, "touch_count_email")
    now = _now_iso()

    try:
        existing = supabase.table("outreach_threads").select(
            f"{col},touch_count_total"
        ).eq("contact_id", contact_id).limit(1).execute()

        if existing.data:
            row = existing.data[0]
            new_chan_count = (row.get(col) or 0) + 1
            new_total = (row.get("touch_count_total") or 0) + 1
            supabase.table("outreach_threads").update({
                col: new_chan_count,
                "touch_count_total": new_total,
                "last_touch_at": now,
                "updated_at": now,
            }).eq("contact_id", contact_id).execute()
        else:
            supabase.table("outreach_threads").upsert({
                "contact_id": contact_id,
                col: 1,
                "touch_count_total": 1,
                "first_touch_at": now,
                "last_touch_at": now,
                "created_at": now,
                "updated_at": now,
            }, on_conflict="contact_id").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("outreach_thread_upsert_error", contact_id=contact_id, error=str(exc))
