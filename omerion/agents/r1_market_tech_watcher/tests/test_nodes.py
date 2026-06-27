"""Unit tests for R1 Market/Tech Watcher — external systems mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

import agents.r1_market_tech_watcher.tools as r1_tools
from agents.r1_market_tech_watcher.graph import (
    dedup_node,
    emit_node,
    fetch_node,
    filter_node,
    index_node,
    persist_node,
    tag_node,
)
from agents.r1_market_tech_watcher.state import RawSignal, TaggedInsight, WatcherState
from agents.r1_market_tech_watcher.tools import (
    _extract_json_object,
    is_relevant,
    persist_insights,
    semantic_dedup,
    tag_signal,
)


@pytest.fixture
def state():
    return WatcherState(session_id="sess-1")


def _raw(title: str = "LangGraph 1.0 released", body: str = "agentic graph update") -> RawSignal:
    return RawSignal(
        source_url=f"https://example.com/{title.replace(' ', '-')}",
        source_type="rss",
        title=title,
        raw_content=body,
    )


def test_is_relevant_matches_keyword():
    assert is_relevant(_raw("LangGraph update", "something"))


def test_is_relevant_rejects_unrelated():
    assert not is_relevant(_raw("Cooking recipes", "tomatoes"))


def test_extract_json_object_from_noise():
    raw = 'answer: {"summary":"s","impact_tag":"internal_os","estimated_priority":"high"} trailing'
    data, ok = _extract_json_object(raw)
    assert data["impact_tag"] == "internal_os"


def test_extract_json_object_garbage():
    data, ok = _extract_json_object("no json here")
    assert data == {}


def test_tag_signal_rejects_invalid_tag():
    router = MagicMock()
    router.complete.return_value = {"text": '{"summary":"s","impact_tag":"bogus","estimated_priority":"high"}'}
    assert tag_signal(router, _raw()) is None


def test_tag_signal_returns_insight_on_valid():
    router = MagicMock()
    router.complete.return_value = {
        "text": '{"summary":"new langgraph","impact_tag":"internal_os","estimated_priority":"high"}'
    }
    out = tag_signal(router, _raw())
    assert out is not None
    assert out.impact_tag == "internal_os"
    assert out.estimated_priority == "high"


def test_fetch_node_populates(state):
    with patch("agents.r1_market_tech_watcher.graph.fetch_signals", return_value=[_raw()]):
        out = fetch_node(state)
    assert len(out.raw) == 1


def test_filter_node_drops_irrelevant(state):
    state.raw = [_raw("LangGraph"), _raw("unrelated topic", "unrelated")]
    with patch("agents.r1_market_tech_watcher.graph.is_relevant", side_effect=[True, False]):
        out = filter_node(state)
    assert len(out.raw) == 1


def test_tag_node_skips_when_empty(state):
    with patch("agents.r1_market_tech_watcher.graph.ClaudeRouter") as R:
        tag_node(state)
    R.assert_not_called()


def test_tag_node_appends_insights(state):
    state.raw = [_raw()]
    ins = TaggedInsight(
        source_url="u", source_type="rss", title="t", summary="s",
        impact_tag="internal_os", estimated_priority="high",
    )
    with patch("agents.r1_market_tech_watcher.graph.ClaudeRouter"), \
         patch("agents.r1_market_tech_watcher.graph.tag_signal", return_value=ins):
        out = tag_node(state)
    assert len(out.insights) == 1


def test_persist_node_sets_counts(state):
    state.insights = [TaggedInsight(
        source_url="u", source_type="rss", title="t", summary="s",
        impact_tag="internal_os", estimated_priority="high",
    )]
    with patch("agents.r1_market_tech_watcher.graph.persist_insights", return_value=(1, 0)):
        out = persist_node(state)
    assert out.inserted == 1


def test_persist_insights_empty_is_noop():
    assert persist_insights([]) == (0, 0)


def test_index_node_filters_unwritten(state):
    wrote = TaggedInsight(
        source_url="u", source_type="rss", title="t", summary="s",
        impact_tag="internal_os", estimated_priority="high",
        insight_id=uuid4(),
    )
    unwritten = TaggedInsight(
        source_url="u2", source_type="rss", title="t2", summary="s2",
        impact_tag="internal_os", estimated_priority="low",
    )
    state.insights = [wrote, unwritten]
    with patch("agents.r1_market_tech_watcher.graph.index_insights") as idx:
        index_node(state)
    idx.assert_called_once()
    passed = idx.call_args[0][0]
    assert len(passed) == 1 and passed[0].insight_id == wrote.insight_id


def test_emit_node_skips_when_nothing_inserted(state):
    state.inserted = 0
    with patch("agents.r1_market_tech_watcher.graph.emit_event") as e:
        emit_node(state)
    e.assert_not_called()


def test_emit_node_emits_when_inserted(state):
    state.inserted = 2
    state.insights = [TaggedInsight(
        source_url="u", source_type="rss", title="t", summary="s",
        impact_tag="internal_os", estimated_priority="high",
        insight_id=uuid4(),
    )]
    with patch("agents.r1_market_tech_watcher.graph.emit_event") as e, \
         patch("omerion_core.runtime.agent_coordinator.mark_agent_complete"):
        emit_node(state)
    e.assert_called_once()


# ── Dual-threshold semantic dedup (the day-one design, finally built) ─────────

def _insight(title: str, summary: str) -> TaggedInsight:
    return TaggedInsight(source_url=f"https://x/{title}", source_type="rss",
                         title=title, summary=summary, impact_tag="daam")


def test_semantic_dedup_hard_skips_near_identical():
    with patch.object(r1_tools, "embed", return_value=[1.0, 0.0]), \
         patch.object(r1_tools, "_pinecone_nearest", return_value=0.97):
        kept, hard = semantic_dedup([_insight("A", "story a")])
    assert kept == [] and hard == 1


def test_semantic_dedup_soft_flags_metadata():
    with patch.object(r1_tools, "embed", return_value=[1.0, 0.0]), \
         patch.object(r1_tools, "_pinecone_nearest", return_value=0.92):
        kept, hard = semantic_dedup([_insight("B", "story b")])
    assert len(kept) == 1 and hard == 0
    assert kept[0].metadata.get("near_duplicate") is True
    assert kept[0].metadata.get("nearest_score") == 0.92


def test_semantic_dedup_clear_passes_untouched():
    with patch.object(r1_tools, "embed", return_value=[1.0, 0.0]), \
         patch.object(r1_tools, "_pinecone_nearest", return_value=0.5):
        kept, hard = semantic_dedup([_insight("C", "story c")])
    assert len(kept) == 1 and hard == 0 and "near_duplicate" not in kept[0].metadata


def test_semantic_dedup_catches_intra_batch_duplicate():
    with patch.object(r1_tools, "embed", return_value=[1.0, 0.0, 0.0]), \
         patch.object(r1_tools, "_pinecone_nearest", return_value=0.0):
        kept, hard = semantic_dedup([_insight("D1", "same"), _insight("D2", "same")])
    assert len(kept) == 1 and hard == 1


def test_dedup_node_sets_state_counter(state):
    state.insights = [_insight("E", "e")]
    with patch("agents.r1_market_tech_watcher.graph.semantic_dedup", return_value=([], 2)):
        out = dedup_node(state)
    assert out.semantic_duplicates == 2 and out.insights == []


# ── Canonical impact_tag validation ───────────────────────────────────────────

def test_impact_tag_accepts_capa_and_remi():
    from pydantic import ValidationError
    from agents.r1_market_tech_watcher.state import TaggedInsight
    for tag in ("daam", "capa", "remi", "asap", "internal_os"):
        ins = TaggedInsight(source_url="u", source_type="rss", title="t",
                            summary="s", impact_tag=tag)
        assert ins.impact_tag == tag


def test_impact_tag_rejects_retired():
    from pydantic import ValidationError
    from agents.r1_market_tech_watcher.state import TaggedInsight
    for retired in ("oria", "rora"):
        with pytest.raises(ValidationError):
            TaggedInsight(source_url="u", source_type="rss", title="t",
                          summary="s", impact_tag=retired)


def test_enrich_article_returns_content_when_httpx_works():
    """enrich_article_content returns content when _stream_bytes succeeds."""
    from agents.r1_market_tech_watcher.tools import enrich_article_content

    body = ("Full article content about LangGraph automation " * 20).encode()

    # Patch _is_safe_url and _stream_bytes so no real network calls are made.
    # _stream_bytes is the streaming primitive; patching it sidesteps httpx mock complexity.
    mock_head_resp = MagicMock()
    mock_head_resp.status_code = 200

    with patch("agents.r1_market_tech_watcher.tools._is_safe_url", return_value=True), \
         patch("agents.r1_market_tech_watcher.tools._stream_bytes", return_value=body), \
         patch("agents.r1_market_tech_watcher.tools.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.head.return_value = mock_head_resp
        mock_httpx.Client.return_value = mock_client
        result = enrich_article_content("https://example.com/article")

    assert len(result) > 200


def test_enrich_article_returns_empty_on_error():
    """enrich_article_content returns empty string on any exception — never raises."""
    from agents.r1_market_tech_watcher.tools import enrich_article_content

    with patch("agents.r1_market_tech_watcher.tools._is_safe_url", return_value=True), \
         patch("agents.r1_market_tech_watcher.tools.httpx") as mock_httpx:
        mock_httpx.Client.side_effect = Exception("network error")
        result = enrich_article_content("https://example.com/article")

    assert result == ""
