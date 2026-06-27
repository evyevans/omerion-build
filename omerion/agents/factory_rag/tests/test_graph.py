"""Test factory_rag graph compile + basic routing."""
import pytest


def test_graph_compiles():
    """build() should return a compiled graph without errors."""
    from agents.factory_rag.graph import build
    g = build()
    assert g is not None


def test_graph_routes_unknown_trigger_to_end():
    """Unknown trigger_type → graph exits without crashing."""
    from agents.factory_rag.graph import build
    from unittest.mock import patch, MagicMock

    g = build()
    with patch("agents.factory_rag.graph.ClaudeRouter") as mock_router_cls:
        mock_router = MagicMock()
        mock_router.complete.return_value = {"text": '{"wartt_summary": "test"}'}
        mock_router_cls.return_value = mock_router
        with patch("agents.factory_rag.graph.upsert_factory_documents", return_value=0), \
             patch("agents.factory_rag.graph.emit_event"):
            state = g.invoke({
                "trigger_type": "unknown_trigger",
                "source_id": "dep-123",
                "industry": "saas",
                "documents_to_ingest": [],
                "ingested_count": 0,
                "pruned_count": 0,
            })
    assert isinstance(state, dict)
