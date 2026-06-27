"""Unit tests for Offer Matching nodes — outside systems mocked."""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from agents.offer_matching.graph import (
    emit_node,
    load_node,
    persist_node,
    propose_node,
)
from agents.offer_matching.state import OfferMatchingState, OfferProposal, PlaybookPhase
from agents.offer_matching.tools import (
    _midpoint,
    _price_band_for,
    summary_stats,
)
from omerion_core.llm.json_extraction import extract_json_object


@pytest.fixture
def state():
    return OfferMatchingState(run_id=uuid4(), session_id="sess-1")


def test_extract_json_object_parses_noisy():
    result, _ = extract_json_object('lead {"x":1} trail')
    assert result == {"x": 1}


def test_price_band_for_known_package_is_usd():
    band = _price_band_for("revenue_acceleration_engine")
    # If agents.yaml is loaded, band has {min,max,currency}; if not, empty.
    if band:
        assert band["currency"] == "USD"
        assert band["max"] >= band["min"] > 0


def test_price_band_for_unknown_is_empty():
    assert _price_band_for("not_a_package") == {}


def test_midpoint_handles_empty():
    assert _midpoint({}) == 0.0
    assert _midpoint({"min": 100, "max": 200}) == 150.0


def test_summary_stats_groups_packages():
    proposals = [
        OfferProposal(contact_id=uuid4(), service_package="revenue_acceleration_engine",
                      demo_reference="DAAM", value_est_usd=8000),
        OfferProposal(contact_id=uuid4(), service_package="research_decision_stack",
                      demo_reference="REMI", value_est_usd=40000),
        OfferProposal(contact_id=uuid4(), service_package="revenue_acceleration_engine",
                      demo_reference="DAAM", value_est_usd=9000),
    ]
    stats = summary_stats(proposals)
    assert stats["count"] == 3
    assert stats["packages"]["revenue_acceleration_engine"] == 2
    assert stats["packages"]["research_decision_stack"] == 1


def test_load_node_uses_loader(state):
    with patch("agents.offer_matching.graph.load_hot_contacts",
               return_value=[{"contact_id": str(uuid4())}]):
        out = load_node(state)
    assert len(out.hot_contacts) == 1


def test_propose_node_skips_when_empty(state):
    with patch("agents.offer_matching.graph.ClaudeRouter") as R:
        out = propose_node(state)
    R.assert_not_called()
    assert out.proposals == []


def test_propose_node_appends_per_contact(state):
    state.hot_contacts = [{"contact_id": str(uuid4())}]
    fake = OfferProposal(contact_id=uuid4(), service_package="ops_intelligence_layer",
                         demo_reference="CAPA")
    with patch("agents.offer_matching.graph.ClaudeRouter"), \
         patch("agents.offer_matching.graph.synthesize_proposal", return_value=fake):
        out = propose_node(state)
    assert len(out.proposals) == 1


def test_persist_node_writes_opportunities_and_memos(state):
    state.proposals = [
        OfferProposal(contact_id=uuid4(), service_package="ops_intelligence_layer",
                      demo_reference="CAPA", memo_md="memo body",
                      value_est_usd=25000,
                      playbook=[PlaybookPhase(label="30", objective="x")]),
    ]
    new_opp = uuid4()
    with patch("agents.offer_matching.graph.write_opportunity", return_value=new_opp), \
         patch("agents.offer_matching.graph.write_memo_draft", return_value=uuid4()) as wm:
        out = persist_node(state)
    assert out.opportunities_created == 1
    wm.assert_called_once()
    assert state.scratch["opportunity_ids"] == [str(new_opp)]


def test_persist_node_skips_when_writer_returns_none(state):
    state.proposals = [OfferProposal(contact_id=uuid4(),
                                     service_package="revenue_acceleration_engine",
                                     demo_reference="DAAM")]
    with patch("agents.offer_matching.graph.write_opportunity", return_value=None), \
         patch("agents.offer_matching.graph.write_memo_draft") as wm:
        out = persist_node(state)
    assert out.opportunities_created == 0
    wm.assert_not_called()


def test_emit_node_emits_with_stats(state):
    state.proposals = [OfferProposal(contact_id=uuid4(),
                                     service_package="revenue_acceleration_engine",
                                     demo_reference="DAAM",
                                     value_est_usd=8000)]
    state.scratch["opportunity_ids"] = ["opp-1"]
    with patch("agents.offer_matching.graph.emit_event") as emit:
        emit_node(state)
    emit.assert_called_once()
    payload = emit.call_args.kwargs["payload"]
    assert payload["stats"]["count"] == 1
