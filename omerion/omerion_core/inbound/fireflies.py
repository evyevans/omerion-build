"""Inbound Fireflies webhook — kicks off Meeting Intelligence.

Fireflies POSTs `transcript.completed` when a recording is fully processed.
Signature is HMAC-SHA256 over the raw body with `FIREFLIES_WEBHOOK_SECRET`.
"""
from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from omerion_core.inbound.rate_limit import limit
from omerion_core.inbound.signatures import verify_fireflies_signature
from omerion_core.logging import get_logger
from omerion_core.runtime.registry import get_handler

log = get_logger("omerion.inbound.fireflies")

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(limit("fireflies", per_minute=30))],
)


class FirefliesAck(BaseModel):
    thread_id: str | None
    meeting_id: str
    event: str


@router.post("/fireflies", response_model=FirefliesAck)
async def fireflies_webhook(
    background_tasks: BackgroundTasks,
    body_bytes: bytes = Depends(verify_fireflies_signature),
) -> FirefliesAck:
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid json") from exc

    event = payload.get("event") or payload.get("eventType") or ""
    if event not in ("transcript.completed", "Transcription completed", "transcription_completed"):
        log.info("fireflies_event_ignored", event_type=event)
        return FirefliesAck(thread_id=None, meeting_id=payload.get("meetingId", ""), event=event)

    meeting_id = payload.get("meetingId") or payload.get("meeting_id") or ""
    if not meeting_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing meetingId")

    correlation_id = payload.get("correlationId") or str(uuid4())
    thread_id = f"meeting:{meeting_id}:{correlation_id}"

    try:
        get_handler("meeting-intelligence")
    except KeyError as exc:
        log.error("meeting_intelligence_not_registered")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "meeting-intelligence not registered") from exc

    try:
        from omerion_core.runtime import run_lifecycle
        run = run_lifecycle.create_run(
            agent_name="meeting-intelligence",
            source_channel="event",
            inputs={
                "meeting_id": meeting_id,
                "correlation_id": correlation_id,
                "thread_id": thread_id,
                "source": "fireflies",
            },
            triggered_by="event:transcript.completed",
            correlation_id=correlation_id,
        )
    except Exception as exc:
        log.error("fireflies_create_run_failed", error=str(exc))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"failed to queue run: {exc}")

    from omerion_core.runtime.run_executor import execute_run
    background_tasks.add_task(execute_run, run["run_id"])

    log.info("fireflies_thread_started", meeting_id=meeting_id, thread_id=thread_id, run_id=run["run_id"])
    return FirefliesAck(thread_id=thread_id, meeting_id=meeting_id, event=event)
