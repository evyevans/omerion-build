"""Integration test for Fix #2 — RSI schema enforcement.

No external services needed; runs anywhere. Verifies that
`ImprovementProposal` rejects partial PROMPT/RAG/COST proposals at
construction time, and that `core/improvement/applier.on_improvement_proposed`
filters invalid actions without crashing.
"""
from __future__ import annotations

import pytest

from core.improvement.applier import on_improvement_proposed
from core.runtime.event_bus import Event
from core.schemas.base import CoreEventType, ImprovementKind, ImprovementProposal


def test_prompt_kind_requires_file_and_content() -> None:
    with pytest.raises(Exception) as exc:
        ImprovementProposal(
            kind=ImprovementKind.PROMPT,
            target_agent="enrich",
            rationale="missing fields",
        )
    msg = str(exc.value)
    assert "file_path" in msg and "new_content" in msg, (
        "validator should name the missing fields"
    )


def test_cost_kind_requires_file_and_content() -> None:
    with pytest.raises(Exception):
        ImprovementProposal(
            kind=ImprovementKind.COST,
            target_agent="outreach",
            rationale="missing fields",
        )


def test_latency_kind_does_not_require_patch() -> None:
    proposal = ImprovementProposal(
        kind=ImprovementKind.LATENCY,
        target_agent="enrich",
        rationale="parallelize Hunter and Claude",
    )
    assert proposal.file_path is None
    assert proposal.new_content is None


def test_file_path_rejects_escape() -> None:
    with pytest.raises(Exception):
        ImprovementProposal(
            kind=ImprovementKind.PROMPT,
            target_agent="enrich",
            rationale="r",
            file_path="../etc/passwd",
            new_content="x",
        )


@pytest.mark.asyncio
async def test_applier_filters_invalid_proposals(caplog) -> None:
    """on_improvement_proposed should skip invalid proposals without raising."""
    event = Event(
        type=CoreEventType.IMPROVEMENT_PROPOSED,
        client_slug="test",
        correlation_id="c1",
        payload={
            "top_actions": [
                # Invalid (missing file_path/new_content)
                {"kind": "prompt", "target_agent": "enrich",
                 "proposal": {"kind": "prompt", "target_agent": "enrich",
                              "rationale": "r"}},
                # Valid (latency doesn't need patch fields)
                {"kind": "latency", "target_agent": "outreach",
                 "proposal": {"kind": "latency", "target_agent": "outreach",
                              "rationale": "speed it up"}},
            ],
        },
    )
    # Should not raise.
    await on_improvement_proposed(event)
