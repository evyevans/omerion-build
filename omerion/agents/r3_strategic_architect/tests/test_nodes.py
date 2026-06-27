"""Unit tests for R3 Strategic Architect — external systems mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.r3_strategic_architect.graph import (
    emit_node,
    hitl_review_node,
    hitl_wait_node,
    load_node,
    persist_node,
    synthesize_node,
)
from agents.r3_strategic_architect.state import ArchitectState, DesignProposal, SignalBundle
from agents.r3_strategic_architect.tools import (
    _extract_json_array,
    _filter_ids,
    priority_score,
    synthesize_proposals,
)


@pytest.fixture
def state():
    return ArchitectState(session_id="sess-1")


def test_priority_score_ordering():
    assert priority_score("high", "S") > priority_score("high", "XL")
    assert priority_score("low", "S") < priority_score("high", "S")


def test_filter_ids_drops_unknown():
    assert _filter_ids(["a", "b", "c"], {"a", "c"}) == ["a", "c"]


def test_extract_json_array_garbage():
    assert _extract_json_array("nothing") == []


def test_synthesize_empty_signals_returns_empty():
    router = MagicMock()
    sigs = SignalBundle()
    out = synthesize_proposals(router, sigs, 14, "2026-04-15")
    assert out == []
    router.complete.assert_not_called()


def test_synthesize_filters_invalid_module():
    router = MagicMock()
    router.complete.return_value = {"text": (
        '[{"title":"A","problem_statement":"p","hypothesis":"h",'
        '"design_doc_md":"d","target_module":"bogus","impact":"high","effort":"S",'
        '"supporting_insight_ids":[],"supporting_oss_ids":[],"supporting_report_ids":[]}]'
    )}
    sigs = SignalBundle(rd_insights=[{"insight_id": "i1", "title": "t", "summary": "s"}])
    out = synthesize_proposals(router, sigs, 14, "2026-04-15")
    assert out == []


def test_synthesize_ranks_by_priority():
    router = MagicMock()
    router.complete.return_value = {"text": (
        '['
        '{"title":"low-eff-L","problem_statement":"p","hypothesis":"h","design_doc_md":"d",'
        '"target_module":"daam","impact":"low","effort":"L",'
        '"supporting_insight_ids":["i1"],"supporting_oss_ids":[],"supporting_report_ids":[]},'
        '{"title":"high-eff-S","problem_statement":"p","hypothesis":"h","design_doc_md":"d",'
        '"target_module":"internal_os","impact":"high","effort":"S",'
        '"supporting_insight_ids":["i1"],"supporting_oss_ids":[],"supporting_report_ids":[]}'
        ']'
    )}
    sigs = SignalBundle(rd_insights=[{"insight_id": "i1"}])
    out = synthesize_proposals(router, sigs, 14, "2026-04-15")
    assert len(out) == 2
    assert out[0].title == "high-eff-S"


def test_load_node_populates(state):
    sb = SignalBundle(rd_insights=[{"insight_id": "x"}])
    with patch("agents.r3_strategic_architect.graph.load_signals", return_value=sb):
        out = load_node(state)
    assert len(out.signals.rd_insights) == 1


def test_synthesize_node_uses_router(state):
    props = [DesignProposal(
        title="x", problem_statement="p", hypothesis="h", design_doc_md="d",
        target_module="internal_os", impact="high", effort="S", priority_score=1.0,
    )]
    with patch("agents.r3_strategic_architect.graph.ClaudeRouter"), \
         patch("agents.r3_strategic_architect.graph.synthesize_proposals_clustered", return_value=props):
        out = synthesize_node(state)
    assert len(out.proposals) == 1


def test_persist_node_sets_ids(state):
    pid = uuid4()
    state.proposals = [DesignProposal(
        title="x", problem_statement="p", hypothesis="h", design_doc_md="d",
        target_module="daam", impact="medium", effort="M", priority_score=0.45,
    )]
    with patch("agents.r3_strategic_architect.graph.write_proposal", return_value=pid):
        out = persist_node(state)
    assert out.proposals[0].proposal_id == pid
    assert out.proposals_written == 1


def test_hitl_review_node_creates_reviews(state):
    pid = uuid4()
    state.proposals = [DesignProposal(
        title="x", problem_statement="p", hypothesis="h", design_doc_md="d",
        target_module="daam", impact="medium", effort="M", priority_score=0.45,
        proposal_id=pid,
    )]
    with patch("agents.r3_strategic_architect.graph.create_founder_review_task",
               return_value={"review_id": uuid4(), "approve_url": "", "reject_url": "", "correlation_id": uuid4()}):
        out = hitl_review_node(state)
    assert out.proposals[0].review_id is not None


def test_hitl_wait_node_marks_decisions(state):
    pid = uuid4()
    rid = uuid4()
    state.proposals = [DesignProposal(
        title="x", problem_statement="p", hypothesis="h", design_doc_md="d",
        target_module="daam", impact="medium", effort="M", priority_score=0.45,
        proposal_id=pid, review_id=rid,
    )]
    with patch("agents.r3_strategic_architect.graph.interrupt",
               return_value={"decisions": {str(rid): "approved"}}), \
         patch("agents.r3_strategic_architect.graph.mark_proposal_decision") as mark:
        out = hitl_wait_node(state)
    assert out.proposals[0].decision == "approved"
    mark.assert_called_once()


def test_emit_node_only_fires_for_approved(state):
    pid1, pid2 = uuid4(), uuid4()
    state.proposals = [
        DesignProposal(title="a", problem_statement="", hypothesis="", design_doc_md="",
                       target_module="daam", impact="high", effort="S",
                       proposal_id=pid1, decision="approved"),
        DesignProposal(title="b", problem_statement="", hypothesis="", design_doc_md="",
                       target_module="daam", impact="high", effort="S",
                       proposal_id=pid2, decision="rejected"),
    ]
    with patch("agents.r3_strategic_architect.graph.emit_event") as e:
        emit_node(state)
    assert e.call_count == 1


# ── Replay-idempotent HITL (no duplicate cards on crash-replay) ──────────────

def _proposal(pid):
    return DesignProposal(title="T", problem_statement="p", hypothesis="h", design_doc_md="d",
                          target_module="internal_os", impact="high", effort="M", proposal_id=pid)


def test_hitl_review_reuses_existing_review_on_replay():
    pid = uuid4()
    s = ArchitectState(run_id=uuid4(), session_id="sess-r")
    s.proposals = [_proposal(pid)]
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{"review_id": "rev-existing", "draft_ref": {"proposal_id": str(pid)}}])
    with patch("omerion_core.clients.supabase_client.supabase", sb), \
         patch("agents.r3_strategic_architect.graph.create_founder_review_task") as cft:
        out = hitl_review_node(s)
    cft.assert_not_called()                       # reused — no duplicate card
    assert str(out.proposals[0].review_id) == "rev-existing"


def test_hitl_review_creates_when_no_existing():
    pid = uuid4()
    s = ArchitectState(run_id=uuid4(), session_id="sess-n")
    s.proposals = [_proposal(pid)]
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    with patch("omerion_core.clients.supabase_client.supabase", sb), \
         patch("agents.r3_strategic_architect.graph.create_founder_review_task", return_value={"review_id": "rev-new"}) as cft:
        out = hitl_review_node(s)
    cft.assert_called_once()
    assert str(out.proposals[0].review_id) == "rev-new"


def test_hitl_wait_fail_closed_rejects_missing_decision():
    s = ArchitectState(run_id=uuid4(), session_id="s3")
    p = _proposal(uuid4()); p.review_id = uuid4()
    s.proposals = [p]
    with patch("agents.r3_strategic_architect.graph.interrupt", return_value={"decisions": {}}), \
         patch("agents.r3_strategic_architect.graph.mark_proposal_decision"):
        out = hitl_wait_node(s)
    assert out.proposals[0].decision == "rejected"


# ── Canonical target_module validation ────────────────────────────────────────

def test_r3_target_module_accepts_capa_and_remi():
    from agents.r3_strategic_architect.state import DesignProposal
    for tag in ("daam", "capa", "remi", "asap", "internal_os"):
        dp = DesignProposal(title="t", problem_statement="p", hypothesis="h",
                            design_doc_md="d", target_module=tag,
                            impact="medium", effort="M")
        assert dp.target_module == tag


def test_r3_target_module_rejects_retired():
    import pytest
    from pydantic import ValidationError
    from agents.r3_strategic_architect.state import DesignProposal
    for retired in ("oria", "rora"):
        with pytest.raises(ValidationError):
            DesignProposal(title="t", problem_statement="p", hypothesis="h",
                           design_doc_md="d", target_module=retired,
                           impact="medium", effort="M")
