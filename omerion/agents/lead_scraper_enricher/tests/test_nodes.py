"""Unit tests for lead_scraper_enricher nodes — all external calls patched.

Post-pivot: scrape/classify collapsed into one autonomous `cognition_node`; the
first-time-scrape gate replaced by the global-policy G2 `hitl_gate_node` (batch
approval before any `contacts` write).
"""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from agents.lead_scraper_enricher.graph import (
    cognition_node,
    emit_node,
    hitl_gate_node,
    load_accounts_node,
    upsert_node,
)
from agents.lead_scraper_enricher.state import EnrichedContact, EnricherState
from omerion_core.hitl.policy import Gate

P = "agents.lead_scraper_enricher.graph."


@pytest.fixture
def account_id():
    return uuid4()


@pytest.fixture
def state(account_id):
    return EnricherState(run_id=uuid4(), session_id="sess-1", account_ids=[account_id])


def test_load_accounts_populates_scratch(state, account_id):
    with patch(P + "load_accounts") as lookup:
        lookup.return_value = [{"account_id": str(account_id), "name": "Acme", "domain": "acme.com"}]
        out = load_accounts_node(state)
    assert str(account_id) in out.scratch["accounts"]


def test_cognition_fans_out_and_tracks_cost(state, account_id):
    state.scratch["accounts"] = {str(account_id): {"account_id": str(account_id), "name": "Acme", "domain": "acme.com"}}
    contact = EnrichedContact(account_id=account_id, full_name="Jane Doe", persona="ops_leader",
                              source="linkedin", source_url="u")
    with patch(P + "ClaudeRouter"), \
         patch(P + "enrich_account", return_value=([contact], 0.08)):
        out = cognition_node(state)
    assert len(out.enriched) == 1
    assert round(out.enrichment_cost_usd, 2) == 0.08


def test_hitl_gate_uses_g2_batch_and_sets_approval(state, account_id):
    state.scratch["accounts"] = {str(account_id): {"account_id": str(account_id), "name": "Acme"}}
    state.enriched = [EnrichedContact(account_id=account_id, full_name="Jane Doe", persona="ops_leader",
                                      source="linkedin", source_url="u")]
    with patch(P + "gate", return_value={"sess-1": "approved"}) as g:
        out = hitl_gate_node(state)
    g.assert_called_once()
    args = g.call_args.args
    assert args[0] == Gate.EXTERNAL_PEOPLE_DATA_WRITE          # G2
    assert len(args[1]) == 1 and args[1][0].key == "sess-1"    # one batch card
    assert out.batch_approved is True


def test_upsert_is_noop_when_unapproved(state, account_id):
    state.enriched = [EnrichedContact(account_id=account_id, full_name="A", source="s", source_url="u")]
    state.batch_approved = False
    with patch(P + "upsert_contact") as up:
        out = upsert_node(state)
    up.assert_not_called()
    assert out.upserted == 0


def test_upsert_counts_duplicates_when_approved(state, account_id):
    state.batch_approved = True
    state.enriched = [
        EnrichedContact(account_id=account_id, full_name="A", email="a@x.com", source="s", source_url="u"),
        EnrichedContact(account_id=account_id, full_name="B", email="b@x.com", source="s", source_url="u"),
    ]
    with patch(P + "upsert_contact") as up:
        up.side_effect = [uuid4(), None]
        out = upsert_node(state)
    assert out.upserted == 1
    assert out.duplicates_skipped == 1


def test_emit_skips_unpersisted(state, account_id):
    state.enriched = [EnrichedContact(account_id=account_id, full_name="A", source="s", source_url="u")]
    with patch(P + "emit_event") as emit:
        out = emit_node(state)
    assert emit.call_count == 0
    assert out.emitted_events == 0
