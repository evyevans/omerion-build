"""TRAINER v2 unit tests — Wave 5 v2.

Focus on the new pure-Python primitives that don't need Supabase or LLM:
  * route_proposal() decision matrix
  * cluster_failures() graceful degradation
  * extract_load_bearing_clauses() heuristic
  * _format_*_block() helpers (LLM input rendering)
  * ShadowEvalResult dataclass shape

Replay + Supabase round-trip tests live in integration/ (require a
live test DB + mocked LLM).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_OMERION = Path(__file__).resolve().parents[3]
if str(_OMERION) not in sys.path:
    sys.path.insert(0, str(_OMERION))


# ─────────────────────────── route_proposal ───────────────────────────


class TestRouteProposal:
    """The 4-branch (really 5-branch) decision matrix."""

    def _result(self, **overrides):
        from agents.trainer.shadow_eval import ShadowEvalResult
        defaults = dict(
            failure_cohort_size=10,
            success_cohort_size=30,
            failure_cohort_fixed=5,
            success_cohort_kept=29,
            fix_rate=0.5,
            regression_rate=0.033,
            net_improvement=0.467,
            insufficient_data=False,
        )
        defaults.update(overrides)
        return ShadowEvalResult(**defaults)

    def test_strong_improvement_auto_promotes(self):
        from agents.trainer.shadow_eval import route_proposal
        r = self._result(fix_rate=0.8, regression_rate=0.03)
        assert route_proposal(r) == "auto_promote"

    def test_marginal_improvement_amber(self):
        from agents.trainer.shadow_eval import route_proposal
        r = self._result(fix_rate=0.4, regression_rate=0.08)
        assert route_proposal(r) == "review_amber"

    def test_high_regression_auto_rejects(self):
        """Regression > 15% triggers auto-reject even with high fix rate.
        This is the catastrophic-forgetting guarantee."""
        from agents.trainer.shadow_eval import route_proposal
        r = self._result(fix_rate=0.9, regression_rate=0.20)
        assert route_proposal(r) == "auto_reject_high_regress"

    def test_low_fix_auto_rejects(self):
        from agents.trainer.shadow_eval import route_proposal
        r = self._result(fix_rate=0.10, regression_rate=0.0)
        assert route_proposal(r) == "auto_reject_low_fix"

    def test_insufficient_data_routes_to_founder(self):
        from agents.trainer.shadow_eval import route_proposal
        r = self._result(insufficient_data=True)
        assert route_proposal(r) == "review_insufficient_data"

    def test_regression_check_runs_before_fix_check(self):
        """Even if fix_rate is 1.0, regression > 15% wins → auto-reject.
        Order of checks matters: regression is the dealbreaker."""
        from agents.trainer.shadow_eval import route_proposal
        r = self._result(fix_rate=1.0, regression_rate=0.30)
        assert route_proposal(r) == "auto_reject_high_regress"


# ─────────────────────────── cluster_failures ─────────────────────────


class TestClusterFailures:
    def test_too_few_samples_returns_unavailable(self):
        from agents.trainer.clustering import cluster_failures
        report = cluster_failures([], min_samples=2)
        assert report.clustering_unavailable
        assert "too few samples" in (report.fallback_reason or "").lower()

    def test_format_for_llm_unavailable_path(self):
        from agents.trainer.clustering import ClusterReport
        r = ClusterReport(
            total_failures=5,
            clustering_unavailable=True,
            fallback_reason="sklearn_import_failed: …",
        )
        out = r.format_for_llm()
        assert "unavailable" in out.lower()
        assert "5" in out  # total_failures surfaced

    def test_format_for_llm_empty_clusters_path(self):
        from agents.trainer.clustering import ClusterReport
        r = ClusterReport(total_failures=4, clusters=[], noise_count=4)
        out = r.format_for_llm()
        assert "unrelated" in out.lower()

    def test_format_for_llm_with_clusters(self):
        from agents.trainer.clustering import ClusterReport, FailureCluster
        r = ClusterReport(
            total_failures=10,
            noise_count=2,
            clusters=[
                FailureCluster(
                    cluster_id=0,
                    size=6,
                    representative_input="contact with no funding signal",
                    representative_response="(LLM refused — no evidence)",
                    sample_invocation_ids=["a", "b", "c"],
                ),
                FailureCluster(
                    cluster_id=1,
                    size=2,
                    representative_input="non-English company name",
                    representative_response="(garbled)",
                ),
            ],
        )
        out = r.format_for_llm(max_clusters=2)
        assert "CLUSTER 0" in out
        assert "6/10" in out
        assert "60%" in out
        assert "funding signal" in out


# ───────────────────────── load-bearing clauses ───────────────────────


class TestLoadBearingClauses:
    def test_extracts_must_clauses(self):
        from agents.trainer.tools import extract_load_bearing_clauses
        prompt = (
            "You are an outreach agent. "
            "You MUST cite a specific recent business event. "
            "You MUST NEVER invent funding rounds. "
            "Use the prospect's name."
        )
        out = extract_load_bearing_clauses(prompt)
        # Should pick up both MUST clauses but NOT "Use the prospect's name."
        assert any("MUST cite" in c for c in out)
        assert any("MUST NEVER" in c or "NEVER invent" in c for c in out)
        assert not any("prospect's name" in c.lower() for c in out)

    def test_caps_clause_length(self):
        from agents.trainer.tools import extract_load_bearing_clauses
        # 600-char "must" clause — should be excluded (too long)
        long_clause = "You must do this: " + ("X " * 300) + "."
        out = extract_load_bearing_clauses(long_clause)
        assert out == []

    def test_caps_clause_count(self):
        from agents.trainer.tools import extract_load_bearing_clauses
        prompt = " ".join(
            f"You must do thing number {i} carefully." for i in range(20)
        )
        out = extract_load_bearing_clauses(prompt, max_clauses=5)
        assert len(out) <= 5

    def test_empty_prompt(self):
        from agents.trainer.tools import extract_load_bearing_clauses
        assert extract_load_bearing_clauses("") == []


# ───────────────────────── meta-prompt formatters ─────────────────────


class TestMetaPromptFormatters:
    def test_empty_failure_samples(self):
        from agents.trainer.tools import _format_failure_samples_block
        out = _format_failure_samples_block([])
        assert "no failure samples" in out.lower()

    def test_failure_samples_block_includes_error_class(self):
        from agents.trainer.shadow_eval import InvocationSample
        from agents.trainer.tools import _format_failure_samples_block
        samples = [
            InvocationSample(
                invocation_id="x",
                rendered_input_text="some input",
                original_response="bad response",
                original_success=False,
                original_cost_usd=0.01,
                original_tokens_out=100,
                error_class="StyleViolation",
                error_message="banned phrase",
            ),
        ]
        out = _format_failure_samples_block(samples)
        assert "StyleViolation" in out
        assert "banned phrase" in out


# ───────────────────────── ShadowEvalResult ───────────────────────────


class TestShadowEvalResultShape:
    """Verify the result dataclass exposes what the HITL card reads."""

    def test_default_construction(self):
        from agents.trainer.shadow_eval import ShadowEvalResult
        r = ShadowEvalResult()
        # All required fields default to zero/empty so partial-failure
        # paths can construct a valid result.
        assert r.fix_rate == 0.0
        assert r.regression_rate == 0.0
        assert r.net_improvement == 0.0
        assert r.failure_cohort_size == 0
        assert not r.insufficient_data
        assert r.regression_samples == []

    def test_cohort_size_constants_exposed(self):
        """Tests inject custom thresholds — verify the constants exist."""
        from agents.trainer.shadow_eval import (
            MIN_FAILURE_COHORT,
            MIN_SUCCESS_COHORT,
        )
        assert MIN_FAILURE_COHORT >= 1
        assert MIN_SUCCESS_COHORT >= MIN_FAILURE_COHORT


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
