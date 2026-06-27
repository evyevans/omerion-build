"""Unit tests for Outcome Attribution nodes — external systems mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.outcome_attribution.graph import (
    deltas_node,
    emit_node,
    feedback_node,
    load_node,
    persist_feedback_node,
    persist_report_node,
    summarize_node,
)
from agents.outcome_attribution.state import AttributionState, FeedbackItem, KpiDelta
from agents.outcome_attribution.tools import (
    _extract_json_array,
    _window,
    derive_proof_point,
)


@pytest.fixture
def state():
    return AttributionState(
        run_id=uuid4(),
        session_id="sess-1",
        deployment_id=uuid4(),
        go_live_at="2026-03-01T00:00:00+00:00",
        persona="team_lead",
        window_days=30,
    )


def test_window_splits_around_anchor():
    pre_s, pre_e, post_s, post_e = _window("2026-03-01T00:00:00+00:00", 30)
    assert pre_e == post_s
    assert pre_s < pre_e < post_e


def test_proof_point_picks_largest_significant():
    deltas = [
        KpiDelta(name="a", pre_mean=10, post_mean=11, delta_abs=1, delta_pct=0.1,
                 sample_pre=5, sample_post=5, significant=True),
        KpiDelta(name="b", pre_mean=20, post_mean=30, delta_abs=10, delta_pct=0.5,
                 sample_pre=5, sample_post=5, significant=True),
        KpiDelta(name="c", pre_mean=100, post_mean=95, delta_abs=-5, delta_pct=-0.05,
                 sample_pre=5, sample_post=5, significant=False),
    ]
    assert derive_proof_point(deltas).startswith("b:")


def test_proof_point_empty_when_no_significant():
    deltas = [KpiDelta(name="a", significant=False)]
    assert derive_proof_point(deltas) == ""


def test_extract_json_array_from_noise():
    raw = 'Here: [{"target":"rd_backlog","recommendation":"x","rationale":"y","confidence":0.8}] done'
    arr = _extract_json_array(raw)
    assert len(arr) == 1 and arr[0]["target"] == "rd_backlog"


def test_extract_json_array_garbage_returns_empty():
    assert _extract_json_array("nothing here") == []


def test_load_node_sets_persona_and_window(state):
    with patch("agents.outcome_attribution.graph.load_deployment") as ld, \
         patch("agents.outcome_attribution.graph.persona_for", return_value="investor"):
        ld.return_value = {
            "deployment_id": str(state.deployment_id),
            "go_live_date": "2026-03-01T00:00:00+00:00",
            "client_id": str(uuid4()),
        }
        out = load_node(state)
    assert out.persona == "investor"
    assert out.window_days >= 1


def test_deltas_node_populates(state):
    state.client_id = uuid4()
    with patch("agents.outcome_attribution.graph.compute_deltas") as cd, \
         patch("agents.outcome_attribution.graph.sum_revenue", side_effect=[1000.0, 1500.0]), \
         patch("agents.outcome_attribution.graph.conversion_rate", side_effect=[(0.2, 10), (0.3, 10)]):
        cd.return_value = [KpiDelta(name="speed_to_lead_minutes", significant=True, delta_pct=0.25)]
        out = deltas_node(state)
    assert len(out.kpi_deltas) == 1
    assert out.revenue_pre == 1000.0 and out.revenue_post == 1500.0
    assert out.conversion_rate_post == 0.3


def test_summarize_node_calls_router(state):
    state.kpi_deltas = [KpiDelta(name="x", significant=True, delta_abs=5, delta_pct=0.5,
                                  pre_mean=10, post_mean=15)]
    with patch("agents.outcome_attribution.graph.ClaudeRouter") as R, \
         patch("agents.outcome_attribution.graph.render_summary", return_value="**Headline:** win"):
        R.return_value = MagicMock()
        out = summarize_node(state)
    assert "win" in out.summary_md
    assert out.proof_point.startswith("x:")


def test_feedback_node_respects_empty_deltas(state):
    state.kpi_deltas = [KpiDelta(name="x", significant=False)]
    with patch("agents.outcome_attribution.graph.ClaudeRouter"), \
         patch("agents.outcome_attribution.graph.generate_feedback", return_value=[]):
        out = feedback_node(state)
    assert out.feedback == []


def test_persist_report_node_sets_report_id(state):
    new_id = uuid4()
    with patch("agents.outcome_attribution.graph.write_report", return_value=new_id):
        out = persist_report_node(state)
    assert out.report_id == new_id


def test_persist_feedback_node_writes_rows(state):
    state.feedback = [FeedbackItem(target="rd_backlog", recommendation="r", rationale="why")]
    with patch("agents.outcome_attribution.graph.write_feedback", return_value=1) as wf:
        persist_feedback_node(state)
    wf.assert_called_once()


def test_emit_node_skips_rd_event_without_feedback(state):
    state.kpi_deltas = [KpiDelta(name="x")]
    state.feedback = []
    with patch("agents.outcome_attribution.graph.emit_event") as emit:
        emit_node(state)
    assert emit.call_count == 1  # only attribution.report.ready


def test_emit_node_emits_both_when_feedback_present(state):
    state.report_id = uuid4()
    state.feedback = [FeedbackItem(target="icp_scoring_weights", recommendation="r", rationale="w")]
    with patch("agents.outcome_attribution.graph.emit_event") as emit:
        emit_node(state)
    assert emit.call_count == 2
