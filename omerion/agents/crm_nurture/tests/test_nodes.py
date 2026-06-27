"""Unit tests for CRM Nurture nodes — outside systems mocked.

Post-pivot: the hand-rolled hitl_review + hitl_wait pair is replaced by one
`hitl_gate` routed through the global HITL policy (G1 outbound-to-humans).
Email is the only live channel.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.crm_nurture.graph import (
    draft_node,
    emit_node,
    filter_node,
    hitl_gate_node,
    load_node,
    send_node,
)
from agents.crm_nurture.state import NurtureCandidate, NurtureDraft, NurtureState
from agents.crm_nurture.tools import _split_email
from omerion_core.hitl.policy import Gate

P = "agents.crm_nurture.graph."


@pytest.fixture
def state():
    return NurtureState(run_id=uuid4(), session_id="sess-1")


def test_split_email_extracts_subject_and_body():
    raw = "SUBJECT: Quick Q on your renewals\nBODY:\nHi Sam — saw your team is hiring.\n\nWorth a chat?"
    s, b = _split_email(raw)
    assert s == "Quick Q on your renewals"
    assert b.startswith("Hi Sam")


def test_split_email_handles_garbage():
    s, b = _split_email("just plain text")
    assert s == "Quick note"
    assert "plain text" in b


def test_load_node_uses_loader(state):
    cand = NurtureCandidate(contact_id=uuid4())
    with patch(P + "load_candidates", return_value=[cand]):
        out = load_node(state)
    assert len(out.candidates) == 1


def test_filter_node_drops_not_due(state):
    state.candidates = [NurtureCandidate(contact_id=uuid4()),
                        NurtureCandidate(contact_id=uuid4())]
    with patch(P + "needs_touch", side_effect=[True, False]):
        out = filter_node(state)
    assert len(out.candidates) == 1
    assert out.skipped_cooldown == 1


def test_draft_node_skips_when_no_candidates(state):
    with patch(P + "ClaudeRouter") as R:
        out = draft_node(state)
    R.assert_not_called()
    assert out.drafts == []


def test_draft_node_records_skipped_when_draft_for_returns_none(state):
    state.candidates = [NurtureCandidate(contact_id=uuid4())]
    with patch(P + "ClaudeRouter") as R, patch(P + "draft_for", return_value=None):
        R.return_value = MagicMock()
        out = draft_node(state)
    assert out.drafts == []
    assert out.skipped_stop_condition == 1


def test_hitl_gate_uses_g1_and_sets_decision(state):
    state.drafts = [NurtureDraft(contact_id=uuid4(), channel="email", template_key="t",
                                 subject="s", body="b", persona="ops_leader")]
    with patch(P + "gate", return_value={"sess-1": "approved"}) as g:
        out = hitl_gate_node(state)
    g.assert_called_once()
    args = g.call_args.args
    assert args[0] == Gate.OUTBOUND_TO_HUMANS              # G1
    assert len(args[1]) == 1 and args[1][0].key == "sess-1"
    assert out.decision == "approved"


def test_hitl_gate_fails_closed_when_decision_missing(state):
    state.drafts = [NurtureDraft(contact_id=uuid4(), channel="email", template_key="t",
                                 subject="s", body="b", persona="ops_leader")]
    with patch(P + "gate", return_value={}):
        out = hitl_gate_node(state)
    assert out.decision == "rejected"


def test_send_node_skips_when_lock_unavailable(state):
    cid = uuid4()
    candidate = NurtureCandidate(contact_id=cid, email="a@b.c")
    state.candidates = [candidate]
    state.drafts = [NurtureDraft(contact_id=cid, channel="email", template_key="t",
                                 subject="s", body="b", persona="x")]
    state.decision = "approved"
    with patch(P + "acquire_advisory_lock", return_value=False), \
         patch(P + "deliver") as deliver:
        send_node(state)
    deliver.assert_not_called()
    assert state.sent_count == 0


def test_send_node_records_provider_id_on_success(state):
    cid = uuid4()
    candidate = NurtureCandidate(contact_id=cid, email="a@b.c")
    state.candidates = [candidate]
    state.drafts = [NurtureDraft(contact_id=cid, channel="email", template_key="t",
                                 subject="s", body="b", persona="x")]
    state.decision = "approved"
    with patch(P + "acquire_advisory_lock", return_value=True), \
         patch(P + "deliver", return_value="gmail-1"), \
         patch(P + "log_outbound", return_value="comm-1"):
        send_node(state)
    assert state.sent_count == 1
    assert state.drafts[0].sent_provider_id == "gmail-1"
    assert state.drafts[0].approved is True


def test_send_node_noop_when_rejected(state):
    cid = uuid4()
    state.candidates = [NurtureCandidate(contact_id=cid, email="a@b.c")]
    state.drafts = [NurtureDraft(contact_id=cid, channel="email", template_key="t",
                                 subject="s", body="b", persona="x")]
    state.decision = "rejected"
    with patch(P + "deliver") as deliver:
        send_node(state)
    deliver.assert_not_called()
    assert state.sent_count == 0


def test_emit_node_skips_when_rejected(state):
    state.decision = "rejected"
    state.drafts = [NurtureDraft(contact_id=uuid4(), channel="email", template_key="t",
                                 subject="s", body="b", persona="x", approved=False)]
    with patch(P + "emit_event") as emit:
        emit_node(state)
    emit.assert_not_called()


def test_emit_node_emits_per_approved(state):
    state.decision = "approved"
    state.drafts = [
        NurtureDraft(contact_id=uuid4(), channel="email", template_key="t",
                     subject="s", body="b", persona="x", approved=True),
        NurtureDraft(contact_id=uuid4(), channel="email", template_key="t",
                     subject="s2", body="b2", persona="y", approved=True),
    ]
    with patch(P + "emit_event") as emit:
        emit_node(state)
    assert emit.call_count == 2
