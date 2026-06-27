"""Tests for R3 signal clustering before synthesis."""
import pytest


def _make_insight(tag: str, i: int) -> dict:
    return {
        "insight_id": f"ins-{tag}-{i}",
        "title": f"Title {i}",
        "summary": f"Summary about {tag} automation {i}",
        "impact_tag": tag,
        "estimated_priority": "high",
        "source_url": f"https://example.com/{i}",
        "created_at": "2026-06-14T00:00:00Z",
    }


def test_cluster_signals_by_tag_groups_correctly():
    """Signals must be grouped by impact_tag."""
    from agents.r3_strategic_architect.tools import cluster_signals_by_tag
    from agents.r3_strategic_architect.state import SignalBundle

    signals = SignalBundle(
        rd_insights=[_make_insight("daam", 1), _make_insight("capa", 2), _make_insight("daam", 3)],
        oss_candidates=[{"candidate_id": "oss-1", "impact_tag": "daam", "name": "test-repo",
                         "integration_type": "component", "overall_score": 0.8,
                         "rescore_history": [], "recommendation": "Use it"}],
        attribution_reports=[],
    )
    clusters = cluster_signals_by_tag(signals)
    assert "daam" in clusters
    assert "capa" in clusters
    assert len(clusters["daam"].rd_insights) == 2
    assert len(clusters["capa"].rd_insights) == 1
    assert len(clusters["daam"].oss_candidates) == 1
    assert len(clusters["capa"].oss_candidates) == 0


def test_cluster_signals_by_tag_empty_tags_go_to_fallback():
    """Insights with no impact_tag or unknown tag go into 'internal_os' fallback bucket."""
    from agents.r3_strategic_architect.tools import cluster_signals_by_tag
    from agents.r3_strategic_architect.state import SignalBundle

    signals = SignalBundle(
        rd_insights=[{"insight_id": "x", "title": "T", "summary": "S",
                      "impact_tag": "", "estimated_priority": "low",
                      "source_url": "", "created_at": "2026-06-14T00:00:00Z"}],
        oss_candidates=[],
        attribution_reports=[],
    )
    clusters = cluster_signals_by_tag(signals)
    total_insights = sum(len(b.rd_insights) for b in clusters.values())
    assert total_insights == 1


def test_synthesize_proposals_calls_once_per_cluster():
    """With 2 clusters, synthesize_proposals should be called exactly 2 times."""
    from unittest.mock import patch, MagicMock
    from agents.r3_strategic_architect.tools import synthesize_proposals_clustered
    from agents.r3_strategic_architect.state import SignalBundle

    signals = SignalBundle(
        rd_insights=[_make_insight("daam", 1), _make_insight("capa", 2)],
        oss_candidates=[],
        attribution_reports=[],
    )

    mock_router = MagicMock()
    mock_router.complete.return_value = {"text": "[]"}

    with patch("agents.r3_strategic_architect.tools.synthesize_proposals") as mock_synth:
        mock_synth.return_value = []
        result = synthesize_proposals_clustered(
            router=mock_router,
            signals=signals,
            lookback_days=14,
            run_date="2026-06-14",
        )
    assert mock_synth.call_count == 2
    assert isinstance(result, list)
