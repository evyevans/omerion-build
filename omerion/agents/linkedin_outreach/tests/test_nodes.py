"""Unit tests for LinkedIn Outreach nodes — external systems mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.linkedin_outreach.graph import (
    caps_node,
    draft_node,
    emit_node,
    hitl_review_node,
    hitl_wait_node,
    load_cohort_node,
    plan_node,
    send_node,
)
from agents.linkedin_outreach.state import DraftedMessage, LinkedInOutreachState, PlannedStep


@pytest.fixture
def state():
    return LinkedInOutreachState(
        run_id=uuid4(),
        session_id="sess-1",
    )


def test_load_cohort_filters_via_tool(state):
    with patch("agents.linkedin_outreach.graph.load_cohort") as lc:
        lc.return_value = [{"id": str(uuid4())}]
        out = load_cohort_node(state)
    assert len(out.cohort) == 1


def test_plan_node_uses_planner(state):
    state.cohort = [{"id": str(uuid4())}]
    pid = uuid4()
    with patch("agents.linkedin_outreach.graph.plan_steps") as ps:
        ps.return_value = [PlannedStep(contact_id=pid, track="cold", template_key="t",
                                       step_type="dm", sequence_step=0)]
        out = plan_node(state)
    assert len(out.planned) == 1


def test_caps_node_records_skipped(state):
    state.planned = [PlannedStep(contact_id=uuid4(), track="cold", template_key="t",
                                 step_type="dm", sequence_step=0)]
    with patch("agents.linkedin_outreach.graph.apply_daily_caps", return_value=([], 1)):
        out = caps_node(state)
    assert out.skipped_capped == 1
    assert out.planned == []


def test_draft_node_skips_when_no_planned(state):
    state.planned = []
    with patch("agents.linkedin_outreach.graph.ClaudeRouter") as R:
        out = draft_node(state)
    R.assert_not_called()
    assert out.drafts == []


def test_draft_node_drafts_per_step(state):
    step = PlannedStep(contact_id=uuid4(), track="warm", template_key="warm_reopen_v1",
                       step_type="dm", sequence_step=0)
    state.planned = [step]
    fake_draft = DraftedMessage(step_id=step.step_id, contact_id=step.contact_id,
                                template_key=step.template_key, track="warm",
                                step_type="dm", body="hi", char_count=2)
    with patch("agents.linkedin_outreach.graph.ClaudeRouter") as R, \
         patch("agents.linkedin_outreach.graph.draft_message", return_value=fake_draft):
        R.return_value = MagicMock()
        out = draft_node(state)
    assert len(out.drafts) == 1


def test_hitl_review_creates_review_with_summary(state):
    state.drafts = [DraftedMessage(step_id=uuid4(), contact_id=uuid4(),
                                   template_key="cold_followup_hook_v1", track="cold",
                                   step_type="dm", body="hi")]
    with patch("agents.linkedin_outreach.graph.create_founder_review_task") as cft:
        cft.return_value = {"review_id": "rev-1"}
        hitl_review_node(state)
    assert state.review_id == "rev-1"
    md = cft.call_args.kwargs["context_md"]
    assert "1" in md and "cold_followup_hook_v1" in md


def test_hitl_wait_translates_decision(state):
    state.review_id = "rev-1"
    with patch("agents.linkedin_outreach.graph.interrupt",
               return_value={"decisions": {"rev-1": "rejected"}, "decision_notes": "off-tone"}):
        out = hitl_wait_node(state)
    assert out.decision == "rejected"
    assert out.scratch["decision_notes"] == "off-tone"


def test_send_node_short_circuits_on_rejection(state):
    state.decision = "rejected"
    state.drafts = [DraftedMessage(step_id=uuid4(), contact_id=uuid4(),
                                   template_key="t", track="cold", step_type="dm", body="x")]
    with patch("agents.linkedin_outreach.graph.queue_for_sender") as q:
        send_node(state)
    q.assert_not_called()
    assert state.sent_count == 0


def test_send_node_queues_each_approved(state):
    state.decision = "approved"
    state.drafts = [DraftedMessage(step_id=uuid4(), contact_id=uuid4(),
                                   template_key="t", track="cold", step_type="dm", body="x"),
                    DraftedMessage(step_id=uuid4(), contact_id=uuid4(),
                                   template_key="t", track="cold", step_type="dm", body="y")]
    with patch("agents.linkedin_outreach.graph.queue_for_sender", return_value="comm-1"), \
         patch("agents.linkedin_outreach.graph.log_activity"):
        send_node(state)
    assert state.sent_count == 2
    assert all(d.approved for d in state.drafts)


def test_emit_node_emits_one_per_approved(state):
    state.decision = "approved"
    state.sent_count = 1
    d = DraftedMessage(step_id=uuid4(), contact_id=uuid4(), template_key="t",
                       track="cold", step_type="dm", body="x", approved=True)
    state.drafts = [d]
    with patch("agents.linkedin_outreach.graph.emit_event") as emit:
        emit_node(state)
    emit.assert_called_once()
