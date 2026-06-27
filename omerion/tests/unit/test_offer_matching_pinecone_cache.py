"""Tests for offer_matching Pinecone cache (Task 4: deterministic HITL replay)."""
from __future__ import annotations

from pathlib import Path

_STATE_SRC = (
    Path(__file__).parent.parent.parent / "agents" / "offer_matching" / "state.py"
).read_text()

_TOOLS_SRC = (
    Path(__file__).parent.parent.parent / "agents" / "offer_matching" / "tools.py"
).read_text()

_GRAPH_SRC = (
    Path(__file__).parent.parent.parent / "agents" / "offer_matching" / "graph.py"
).read_text()


def test_state_has_pinecone_cache():
    """OfferMatchingState must declare pinecone_cache field."""
    assert "pinecone_cache" in _STATE_SRC, "pinecone_cache field missing from OfferMatchingState"
    assert "dict" in _STATE_SRC, "pinecone_cache must be a dict type"


def test_synthesize_proposal_accepts_cached_similar():
    """synthesize_proposal must accept cached_similar parameter to avoid re-querying Pinecone."""
    assert "cached_similar" in _TOOLS_SRC, "synthesize_proposal must accept cached_similar param"


def test_propose_node_uses_cache():
    """propose_node must populate pinecone_cache before HITL and use it on replay."""
    assert "pinecone_cache" in _GRAPH_SRC, "propose_node must read/write pinecone_cache"
    assert "find_similar_wins" in _GRAPH_SRC, "graph.py must import and call find_similar_wins"
    assert "cached_similar" in _GRAPH_SRC, "propose_node must pass cached_similar to synthesize_proposal"
