"""Tests for client_intake graph.py (Task 3: fix ClaudeRouter, tuple unpack, EventType enum).

Inspects source text directly to avoid the deep import chain from agents/__init__.py.
"""
from __future__ import annotations

from pathlib import Path

_GRAPH_SRC = (
    Path(__file__).parent.parent.parent / "agents" / "client_intake" / "graph.py"
).read_text()


def test_extract_profile_uses_router_complete():
    """extract_profile must call router.complete(), not llm.invoke()."""
    assert "router.complete(" in _GRAPH_SRC, "Must use router.complete()"
    assert "llm.invoke" not in _GRAPH_SRC, "Must not use llm.invoke()"
    assert "get_model" not in _GRAPH_SRC, "ClaudeRouter.get_model() does not exist"


def test_extract_profile_unpacks_tuple():
    """extract_json_object returns (data, ok) — must be unpacked as a tuple."""
    assert "data, ok" in _GRAPH_SRC, "Must unpack (data, ok) tuple from extract_json_object"


def test_emit_profile_uses_event_type_enum():
    """emit_profile must reference EventType.CLIENT_PROFILE_READY, not a raw string."""
    assert "EventType.CLIENT_PROFILE_READY" in _GRAPH_SRC, "Must use EventType enum"
    assert '"client.profile.ready"' not in _GRAPH_SRC, "Must not use raw event string"


def test_traced_node_decorators_present():
    """All graph nodes must have @traced_node decorator for telemetry."""
    for node in ("load_transcript", "retrieve_similar_profiles", "extract_profile",
                 "validate_completeness", "emit_profile"):
        assert f'@traced_node("{node}")' in _GRAPH_SRC, f"Missing @traced_node on {node}"
