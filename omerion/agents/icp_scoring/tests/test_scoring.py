"""Unit tests for ICP scoring — deterministic sub-scores only."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.icp_scoring.tools import (
    compute_fit,
    compute_timing,
    final_score,
    segment_of,
)
from omerion_core.settings import Settings


@pytest.fixture(autouse=True)
def _ensure_yaml_config():
    """Ensure settings can find agents.yaml from test run location."""
    test_dir = Path(__file__).parent
    project_root = test_dir.parent.parent.parent
    yaml_path = project_root / "config" / "agents.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"agents.yaml not found at {yaml_path}")
    # Force settings to use the absolute path.
    from omerion_core.settings import settings
    settings.agents_config_path = str(yaml_path)
    # Clear the cached agents config so it reloads from the new path.
    from omerion_core.settings import _agents_config_cache
    _agents_config_cache.cache_clear()
    yield


@pytest.fixture
def hot_contact():
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "persona": "sme_founder",
        "title": "Founder / CEO",
        "accounts": {"tier": "A", "team_size_bucket": 50},
        "last_touch_at": None,
    }


@pytest.fixture
def cold_contact():
    return {
        "id": "00000000-0000-0000-0000-000000000002",
        "persona": "unknown",
        "title": "",
        "accounts": {"tier": "D", "team_size_bucket": 1},
        "last_touch_at": None,
    }


def test_fit_owner_outranks_unknown(hot_contact, cold_contact):
    hot, _ = compute_fit(hot_contact)
    cold, _ = compute_fit(cold_contact)
    assert hot > cold


def test_timing_nonnegative():
    val, _ = compute_timing({"last_touch_at": None})
    assert 0.0 <= val <= 1.0


def test_final_score_weighting():
    assert final_score(1.0, 1.0, 1.0) == pytest.approx(1.0, abs=1e-3)
    assert final_score(0.0, 0.0, 0.0) == 0.0


def test_segmentation_thresholds():
    assert segment_of(0.9) == "hot"
    assert segment_of(0.6) == "warm"
    assert segment_of(0.35) == "watchlist"
    assert segment_of(0.1) == "cold"
