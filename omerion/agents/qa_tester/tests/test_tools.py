"""Tests for QA_TESTER deterministic tools."""
import pytest
from agents.qa_tester.tools import parse_pytest_output, coverage_meets_threshold


def test_parse_pytest_output_all_pass():
    raw = "5 passed in 1.23s"
    result = parse_pytest_output(raw)
    assert result["total"] == 5
    assert result["passed"] == 5
    assert result["failed"] == 0


def test_parse_pytest_output_with_failures():
    raw = "3 passed, 2 failed in 2.10s"
    result = parse_pytest_output(raw)
    assert result["total"] == 5
    assert result["passed"] == 3
    assert result["failed"] == 2


def test_parse_pytest_output_empty():
    result = parse_pytest_output("")
    assert result["total"] == 0
    assert result["passed"] == 0
    assert result["failed"] == 0


def test_coverage_meets_threshold_pass():
    assert coverage_meets_threshold(coverage_pct=0.82, threshold=0.70) is True


def test_coverage_meets_threshold_fail():
    assert coverage_meets_threshold(coverage_pct=0.65, threshold=0.70) is False


def test_coverage_meets_threshold_exact():
    assert coverage_meets_threshold(coverage_pct=0.70, threshold=0.70) is True
