"""Unit tests for Market Mapper nodes — outside systems mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.market_mapper.graph import (
    classify_node,
    emit_node,
    rank_node,
    scrape_node,
    seed_node,
    upsert_node,
)
from agents.market_mapper.state import MarketAccount, MarketMapState
from agents.market_mapper.tools import _bucket, _persona_fit, _tech_maturity_score, _volume_score, rank


@pytest.fixture
def state():
    return MarketMapState(
        run_id=uuid4(),
        session_id="sess-1",
        target_markets=["Phoenix"],
    )


def test_volume_score_clamps_at_1():
    a = MarketAccount(name="X", market="Phoenix", source_url="https://x.com", volume_estimate=1000)
    assert _volume_score(a) == 1.0


def test_tech_maturity_score_caps():
    a = MarketAccount(name="X", market="Phoenix", source_url="https://x.com",
                      tech_signals=["a", "b", "c", "d", "e", "f"])
    assert _tech_maturity_score(a) == 1.0


def test_bucket_thresholds():
    assert _bucket(None) is None
    assert _bucket(10) == "lt_25"
    assert _bucket(50) == "lt_100"
    assert _bucket(250) == "lt_500"
    assert _bucket(1000) == "gte_500"


def test_persona_fit_overlap_with_role_terms():
    a = MarketAccount(name="X", market="Phoenix", source_url="https://x.com",
                      raw_metadata={"role_hints": ["Team Lead", "Producing Agent"]})
    score = _persona_fit("team_lead", a)
    assert score > 0


def test_rank_qualifies_when_volume_and_team_meet_floor():
    a = MarketAccount(name="X", market="Phoenix", source_url="https://x.com",
                      volume_estimate=200, team_size=10,
                      tech_signals=["crm", "calendly"])
    out = rank(a, "team_lead")
    assert out.qualifies is True
    assert 0 < out.final_score <= 1


def test_seed_node_pulls_target_markets_when_empty(state):
    state.target_markets = []
    with patch("agents.market_mapper.graph.target_markets", return_value=["A", "B"]):
        out = seed_node(state)
    assert out.target_markets == ["A", "B"]


def test_scrape_node_collects_per_market(state):
    state.target_markets = ["Phoenix", "Denver"]
    fake = [MarketAccount(name="X", market="Phoenix", source_url="https://x.com")]
    with patch("agents.market_mapper.graph.scrape_market", side_effect=[fake, []]):
        out = scrape_node(state)
    assert len(out.candidates) == 1


def test_classify_node_skips_when_empty(state):
    with patch("agents.market_mapper.graph.ClaudeRouter") as R:
        out = classify_node(state)
    R.assert_not_called()
    assert out.candidates == []


def test_classify_node_assigns_persona(state):
    state.candidates = [MarketAccount(name="X", market="Phoenix", source_url="https://x.com")]
    with patch("agents.market_mapper.graph.ClaudeRouter") as R, \
         patch("agents.market_mapper.graph.classify_persona", return_value="investor"):
        R.return_value = MagicMock()
        out = classify_node(state)
    assert out.candidates[0].persona == "investor"


def test_rank_node_runs_for_all(state):
    state.candidates = [MarketAccount(name="X", market="Phoenix", source_url="https://x.com",
                                      volume_estimate=200, team_size=10, persona="team_lead")]
    out = rank_node(state)
    assert out.candidates[0].final_score > 0


def test_upsert_node_skips_below_threshold(state):
    qualified_id = uuid4()
    state.candidates = [
        MarketAccount(name="OK", market="Phoenix", source_url="https://x.com",
                      qualifies=True, persona="team_lead"),
        MarketAccount(name="NO", market="Phoenix", source_url="https://y.com",
                      qualifies=False, persona="unknown"),
    ]
    with patch("agents.market_mapper.graph.upsert_market", return_value=uuid4()), \
         patch("agents.market_mapper.graph.upsert_account", return_value=qualified_id):
        out = upsert_node(state)
    assert out.accounts_upserted == 1
    assert out.accounts_skipped_threshold == 1


def test_emit_node_groups_by_market(state):
    aid = uuid4()
    state.candidates = [
        MarketAccount(name="A", market="Phoenix", source_url="https://x.com",
                      qualifies=True, persona="team_lead", account_id=aid),
        MarketAccount(name="B", market="Denver", source_url="https://y.com",
                      qualifies=True, persona="investor", account_id=uuid4()),
    ]
    with patch("agents.market_mapper.graph.emit_event") as emit:
        emit_node(state)
    assert emit.call_count == 2
