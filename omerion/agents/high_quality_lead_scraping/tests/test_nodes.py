"""Unit tests for High-Quality Lead Scraping nodes — outside systems mocked.

Post-pivot structure: rigid research/synthesize/quality_gate nodes were collapsed
into one autonomous `cognition_node`; the two HITL nodes became one `hitl_gate_node`
routed through the global HITL policy.
"""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from agents.high_quality_lead_scraping.graph import (
    cognition_node,
    emit_node,
    hitl_gate_node,
    load_node,
    persist_node,
)
from agents.high_quality_lead_scraping.state import Dossier, HQLState
from omerion_core.llm.json_extraction import extract_json_object


@pytest.fixture
def state():
    return HQLState(run_id=uuid4(), session_id="sess-1")


# ── JSON extraction (shared util) ────────────────────────────────────────────
def test_extract_json_object_parses_noisy():
    data, ok = extract_json_object('lead-in {"a": 1, "b": [2,3]} trailer')
    assert ok and data == {"a": 1, "b": [2, 3]}


def test_extract_json_object_garbage_returns_empty():
    data, ok = extract_json_object("not json")
    assert not ok and data == {}


# ── load ─────────────────────────────────────────────────────────────────────
def test_load_node_uses_loader(state):
    with patch("agents.high_quality_lead_scraping.graph.load_priority_accounts",
               return_value=[{"account_id": str(uuid4()), "name": "X"}]):
        out = load_node(state)
    assert len(out.accounts) == 1


# ── cognition (autonomous loop + semantic dedup) ─────────────────────────────
def _patch_cognition(*, research_return, dedup_return=("clear", 0.1)):
    return (
        patch("agents.high_quality_lead_scraping.graph.ClaudeRouter"),
        patch("agents.high_quality_lead_scraping.graph.research_account",
              return_value=research_return),
        patch("agents.high_quality_lead_scraping.graph.dedup_status",
              return_value=dedup_return),
    )


def test_cognition_node_appends_clear_dossier_and_tracks_cost(state):
    aid = str(uuid4())
    state.accounts = [{"account_id": aid, "name": "X", "domain": "x.com"}]
    dossier = Dossier(account_id=aid, summary="s", confidence=0.8,
                      pain_signals=["p"] * 3, source_urls=["u"] * 3)
    rc, ra, ds = _patch_cognition(research_return=(dossier, 0.12))
    with rc, ra, ds:
        out = cognition_node(state)
    assert len(out.dossiers) == 1
    assert round(out.research_cost_usd, 2) == 0.12


def test_cognition_node_skips_hard_duplicate(state):
    aid = str(uuid4())
    state.accounts = [{"account_id": aid, "name": "X"}]
    rc, ra, ds = _patch_cognition(research_return=(Dossier(account_id=aid, summary="s"), 0.0),
                                  dedup_return=("duplicate", 0.98))
    with rc, ra, ds:
        out = cognition_node(state)
    assert out.dossiers == []
    assert out.skipped_duplicate == 1


def test_cognition_node_soft_flags_similar(state):
    aid = str(uuid4())
    state.accounts = [{"account_id": aid, "name": "X"}]
    rc, ra, ds = _patch_cognition(research_return=(Dossier(account_id=aid, summary="s"), 0.0),
                                  dedup_return=("similar", 0.92))
    with rc, ra, ds:
        out = cognition_node(state)
    assert len(out.dossiers) == 1
    assert out.dossiers[0].dedup_note  # founder-facing soft flag populated


def test_cognition_node_counts_no_finalize_as_skip(state):
    state.accounts = [{"account_id": str(uuid4()), "name": "X"}]
    # research_account returns (None, cost) when the model never finalized.
    with patch("agents.high_quality_lead_scraping.graph.ClaudeRouter"), \
         patch("agents.high_quality_lead_scraping.graph.research_account",
               return_value=(None, 0.05)):
        out = cognition_node(state)
    assert out.dossiers == []
    assert out.skipped_low_quality == 1
    assert round(out.research_cost_usd, 2) == 0.05


# ── hitl_gate (G2 people-data write, via global policy) ──────────────────────
def test_hitl_gate_applies_founder_decisions(state):
    aid = str(uuid4())
    state.accounts = [{"account_id": aid, "name": "X"}]
    state.dossiers = [Dossier(account_id=aid, summary="s")]
    with patch("agents.high_quality_lead_scraping.graph.gate",
               return_value={aid: "approved"}) as g:
        out = hitl_gate_node(state)
    g.assert_called_once()
    assert out.dossiers[0].decision == "approved"


def test_hitl_gate_fails_closed_when_decision_missing(state):
    aid = str(uuid4())
    state.accounts = [{"account_id": aid, "name": "X"}]
    state.dossiers = [Dossier(account_id=aid, summary="s")]
    with patch("agents.high_quality_lead_scraping.graph.gate", return_value={}):
        out = hitl_gate_node(state)
    assert out.dossiers[0].decision == "rejected"


# ── persist / emit (unchanged) ───────────────────────────────────────────────
def test_persist_node_writes_only_approved(state):
    new_id = uuid4()
    state.dossiers = [
        Dossier(account_id=uuid4(), decision="approved"),
        Dossier(account_id=uuid4(), decision="rejected"),
    ]
    with patch("agents.high_quality_lead_scraping.graph.index_dossier", return_value=["v1"]), \
         patch("agents.high_quality_lead_scraping.graph.write_dossier", return_value=new_id), \
         patch("agents.high_quality_lead_scraping.graph.supabase"):
        out = persist_node(state)
    assert out.dossiers_written == 1
    assert state.dossiers[0].dossier_id == new_id


def test_emit_node_skips_unapproved(state):
    state.dossiers = [Dossier(account_id=uuid4(), decision="rejected", dossier_id=uuid4())]
    with patch("agents.high_quality_lead_scraping.graph.emit_event") as emit:
        emit_node(state)
    emit.assert_not_called()
