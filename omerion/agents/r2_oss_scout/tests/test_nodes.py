"""Unit tests for R2 OSS Scout — external systems mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.r2_oss_scout.graph import (
    analyze_node,
    discover_node,
    emit_node,
    filter_node,
    persist_node,
)
from agents.r2_oss_scout.state import (
    OssScoutState,
    RepoCandidate,
    RubricScore,
    ScoredCandidate,
)
from agents.r2_oss_scout.tools import (
    _extract_json_object,
    _overall,
    analyze_repo,
    passes_floor,
)


@pytest.fixture
def state():
    return OssScoutState(session_id="sess-1")


def _repo(stars: int = 500, license: str = "MIT") -> RepoCandidate:
    return RepoCandidate(
        repo_url=f"https://github.com/x/{stars}",
        name=f"repo-{stars}",
        description="agentic framework",
        stars=stars,
        language="Python",
        license=license,
        readme_excerpt="readme",
        search_tag="agent orchestration",
    )


def test_passes_floor_above_min():
    assert passes_floor(_repo(stars=500))


def test_passes_floor_below_min():
    assert not passes_floor(_repo(stars=10))


def test_overall_weights_composite():
    r = RubricScore(fit=1.0, maturity=1.0, composability=1.0, risk=0.0)
    assert _overall(r) == round(0.4 + 0.25 + 0.25 + 0.1, 3)


def test_extract_json_garbage():
    assert _extract_json_object("no json") == {}


def test_analyze_repo_valid():
    router = MagicMock()
    router.complete.return_value = {"text": (
        '{"fit":0.8,"maturity":0.6,"composability":0.7,"risk":0.1,'
        '"integration_type":"component","impact_tag":"internal_os",'
        '"recommendation":"vendor the router"}'
    )}
    out = analyze_repo(router, _repo())
    assert out is not None
    assert out.integration_type == "component"
    assert out.impact_tag == "internal_os"
    assert 0.0 <= out.rubric.overall <= 1.0


def test_analyze_repo_rejects_invalid_tag():
    router = MagicMock()
    router.complete.return_value = {"text": (
        '{"fit":0.8,"maturity":0.6,"composability":0.7,"risk":0.1,'
        '"integration_type":"invalid","impact_tag":"internal_os"}'
    )}
    assert analyze_repo(router, _repo()) is None


def test_analyze_repo_bumps_risk_on_agpl():
    router = MagicMock()
    router.complete.return_value = {"text": (
        '{"fit":0.9,"maturity":0.9,"composability":0.9,"risk":0.1,'
        '"integration_type":"full_module","impact_tag":"daam"}'
    )}
    out = analyze_repo(router, _repo(license="AGPL-3.0"))
    assert out.rubric.risk >= 0.8


def test_discover_node_populates(state):
    with patch("agents.r2_oss_scout.graph.discover_candidates", return_value=[_repo()]):
        out = discover_node(state)
    assert len(out.raw) == 1


def test_filter_node_drops_below_floor(state):
    state.raw = [_repo(500), _repo(10)]
    with patch("agents.r2_oss_scout.graph.passes_floor", side_effect=[True, False]):
        out = filter_node(state)
    assert len(out.raw) == 1


def test_analyze_node_skips_empty(state):
    with patch("agents.r2_oss_scout.graph.ClaudeRouter") as R:
        analyze_node(state)
    R.assert_not_called()


def test_analyze_node_appends_scored(state):
    state.raw = [_repo()]
    sc = ScoredCandidate(
        repo=_repo(),
        rubric=RubricScore(fit=0.5, maturity=0.5, composability=0.5, risk=0.1, overall=0.45),
        integration_type="component",
        impact_tag="internal_os",
    )
    with patch("agents.r2_oss_scout.graph.ClaudeRouter"), \
         patch("agents.r2_oss_scout.graph.analyze_repo", return_value=sc):
        out = analyze_node(state)
    assert len(out.scored) == 1


def test_persist_node_sets_counts(state):
    with patch("agents.r2_oss_scout.graph.persist_candidates", return_value=(3, 1)):
        out = persist_node(state)
    assert out.inserted == 3 and out.duplicates == 1


def test_emit_node_skips_when_none_inserted(state):
    state.inserted = 0
    with patch("agents.r2_oss_scout.graph.emit_event") as e:
        emit_node(state)
    e.assert_not_called()


def test_emit_node_fires_when_inserted(state):
    state.inserted = 1
    state.scored = [ScoredCandidate(
        repo=_repo(),
        rubric=RubricScore(overall=0.7),
        integration_type="component",
        impact_tag="internal_os",
    )]
    with patch("agents.r2_oss_scout.graph.emit_event") as e:
        emit_node(state)
    e.assert_called_once()


# ── Insight-seeded discovery + tier alignment (Tier.POWER bug fixed) ──────────

import agents.r2_oss_scout.tools as _r2_tools
from agents.r2_oss_scout.tools import seed_terms_from_insight
from omerion_core.llm.router import Tier as _Tier


def test_seed_terms_from_insight():
    assert seed_terms_from_insight("LangGraph 1.0", "internal_os") == [
        "LangGraph 1.0", "agent orchestration framework"]
    assert seed_terms_from_insight("", "") == []


def test_discover_candidates_seeds_focus_search():
    ms = MagicMock(); ms.agent.return_value = {"search_tags": ["RAG", "LangGraph", "webhooks"]}
    with patch.object(_r2_tools, "settings", ms), \
         patch.object(_r2_tools, "search_github", return_value=[]) as sg:
        _r2_tools.discover_candidates(seed_terms=["LangGraph 1.0"])
    q = sg.call_args[0][0]
    assert q[0] == "LangGraph 1.0" and len(q) <= 3


def test_analyze_repo_haiku_base_sonnet_escalation():
    """Base Tier.FAST; escalate to Tier.DEFAULT on risk>0.5 (was the dead Tier.POWER)."""
    calls = []

    def fake(router, repo, tier):
        calls.append(tier)
        risk = 0.9 if tier == _Tier.FAST else 0.2
        return ({"integration_type": "component", "impact_tag": "internal_os",
                 "fit": 0.8, "maturity": 0.7, "composability": 0.6, "risk": risk,
                 "recommendation": "use"}, True)

    with patch.object(_r2_tools, "_analyze_with_tier", side_effect=fake):
        sc = analyze_repo(MagicMock(), _repo())
    assert calls[0] == _Tier.FAST and _Tier.DEFAULT in calls and sc.scored_by == "sonnet"


def test_discover_node_seeds_from_insight_state(state):
    state.insight_title = "Claude 4.5"
    state.insight_impact_tag = "daam"
    with patch("agents.r2_oss_scout.graph.discover_candidates", return_value=[]) as dc:
        out = discover_node(state)
    assert out.seed_terms and dc.call_args.kwargs["seed_terms"] == out.seed_terms


# ── Canonical impact_tag validation ───────────────────────────────────────────

def test_r2_impact_tag_accepts_capa_and_remi():
    from pydantic import ValidationError
    from agents.r2_oss_scout.state import RepoCandidate, RubricScore, ScoredCandidate
    _repo = RepoCandidate(repo_url="u", name="n", source_url="u")
    for tag in ("daam", "capa", "remi", "asap", "internal_os"):
        sc = ScoredCandidate(repo=_repo, rubric=RubricScore(),
                             integration_type="component", impact_tag=tag)
        assert sc.impact_tag == tag


def test_r2_impact_tag_rejects_retired():
    import pytest
    from pydantic import ValidationError
    from agents.r2_oss_scout.state import RepoCandidate, RubricScore, ScoredCandidate
    _repo = RepoCandidate(repo_url="u", name="n", source_url="u")
    for retired in ("oria", "rora"):
        with pytest.raises(ValidationError):
            ScoredCandidate(repo=_repo, rubric=RubricScore(),
                            integration_type="component", impact_tag=retired)
