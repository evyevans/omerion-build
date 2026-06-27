"""Global HITL gate policy — G1/G2/G3 routing through LangGraph interrupt()."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

from langgraph.types import interrupt

from omerion_core.hitl.review import create_founder_review_task
from omerion_core.logging import get_logger

log = get_logger("omerion.hitl.policy")


class Gate(str, Enum):
    OUTBOUND_TO_HUMANS = "G1_outbound_to_humans"
    EXTERNAL_PEOPLE_DATA_WRITE = "G2_external_people_data_write"
    DEPLOY_OR_INFRA = "G3_deploy_or_infra"


@dataclass
class ReviewItem:
    key: str
    subject: str
    context_md: str
    draft_ref: dict[str, Any] = field(default_factory=dict)


def gate(
    gate_kind: Gate,
    items: list[ReviewItem],
    *,
    agent_name: str,
    session_id: str,
    correlation_id: UUID | str | None = None,
) -> dict[str, str]:
    """Create founder review cards and pause until all items are decided."""
    if not items:
        return {}

    key_to_review: dict[str, str] = {}
    review_ids: list[str] = []

    for item in items:
        review = create_founder_review_task(
            agent_name=agent_name,
            session_id=session_id,
            subject=item.subject,
            context_md=item.context_md,
            draft_ref={
                **item.draft_ref,
                "gate": gate_kind.value,
                "item_key": item.key,
            },
            correlation_id=correlation_id,
        )
        review_id = str(review["review_id"])
        key_to_review[item.key] = review_id
        review_ids.append(review_id)

    resume_payload: Any = interrupt({
        "review_ids": review_ids,
        "session_id": session_id,
        "gate": gate_kind.value,
        "items": [item.key for item in items],
    })

    raw_decisions: dict[str, str] = {}
    if isinstance(resume_payload, dict):
        raw_decisions = dict(resume_payload.get("decisions") or {})
        if not raw_decisions and resume_payload.get("review_id"):
            raw_decisions = {
                str(resume_payload["review_id"]): resume_payload.get("decision", "rejected")
            }
        elif not raw_decisions and resume_payload.get("decision") and len(review_ids) == 1:
            raw_decisions = {review_ids[0]: resume_payload.get("decision", "rejected")}

    out: dict[str, str] = {}
    for item in items:
        review_id = key_to_review[item.key]
        decision = raw_decisions.get(review_id) or raw_decisions.get(item.key) or "rejected"
        if decision == "edited":
            decision = "approved"
        out[item.key] = decision

    log.info(
        "hitl_gate_resolved",
        gate=gate_kind.value,
        items=len(items),
        approved=sum(1 for d in out.values() if d == "approved"),
    )
    return out
