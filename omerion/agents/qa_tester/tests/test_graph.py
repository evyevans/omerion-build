"""Smoke tests: graph compiles and routes correctly."""
import pytest
from unittest.mock import patch
from agents.qa_tester.graph import build, route_after_tests
from agents.qa_tester.state import QATesterState


def test_graph_compiles():
    with patch("omerion_core.runtime.checkpointer.get_checkpointer", return_value=None):
        graph = build()
    assert graph is not None


def test_route_after_tests_pass():
    state = QATesterState(agent_name="qa_tester", tests_failed=0, coverage_pct=0.85, coverage_threshold=0.70)
    assert route_after_tests(state) == "qa_gate"


def test_route_after_tests_test_fail():
    state = QATesterState(agent_name="qa_tester", tests_failed=2, coverage_pct=0.85, coverage_threshold=0.70)
    assert route_after_tests(state) == "analyze_failures"


def test_route_after_tests_coverage_fail():
    state = QATesterState(agent_name="qa_tester", tests_failed=0, coverage_pct=0.60, coverage_threshold=0.70)
    assert route_after_tests(state) == "analyze_failures"
