"""TRAINER unit tests — Wave 5.

Focus: the deterministic primitives that gate every LLM proposal.

  * `read_agent_prompts` — must extract uppercase string assigns from
    a prompts.py AST WITHOUT executing the module.
  * `validate_proposal_text` — the three guardrails (code fences,
    class defs, placeholder set).
  * `identify_underperformers` — threshold rule enforcement.
  * `iso_week_key` — stable week label for idempotency.
  * `sha256_hex` — deterministic content hash.

These tests run without Supabase. Tests that need DB go in
`omerion/tests/integration/` (future).
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

# Path bootstrap for running from omerion/ or repo root.
_OMERION = Path(__file__).resolve().parents[3]
if str(_OMERION) not in sys.path:
    sys.path.insert(0, str(_OMERION))


# ─────────────────────────── helpers ──────────────────────────────────


class TestHelpers:
    def test_iso_week_key_format(self):
        from agents.trainer.tools import iso_week_key

        # 2026-05-25 is a Monday → ISO week 22 of 2026.
        wk = iso_week_key(date(2026, 5, 25))
        assert wk == "2026-W22"

    def test_iso_week_key_pads_single_digit(self):
        from agents.trainer.tools import iso_week_key

        # Early-year week → zero-padded.
        wk = iso_week_key(date(2026, 1, 5))
        assert wk.startswith("2026-W")
        assert len(wk.split("-W")[1]) == 2  # always 2 digits

    def test_sha256_hex_deterministic(self):
        from agents.trainer.tools import sha256_hex

        assert sha256_hex("hello") == sha256_hex("hello")
        assert len(sha256_hex("hello")) == 64
        assert sha256_hex("hello") != sha256_hex("world")


# ─────────────────────────── AST prompt reader ────────────────────────


class TestReadAgentPrompts:
    """The single most important safety primitive: extracts prompt
    constants without ever executing the target module."""

    def test_extracts_uppercase_string_assigns(self, tmp_path, monkeypatch):
        # Build a fake agent dir with a prompts.py
        agents_root = tmp_path / "agents"
        fake = agents_root / "fake_agent"
        fake.mkdir(parents=True)
        (fake / "prompts.py").write_text(
            'NURTURE_SYSTEM = "Be concise. Use {persona}."\n'
            'NURTURE_USER = "Draft for {contact_name}."\n'
            'private_var = "should not extract"\n'
            'lowercase_const = "also not extracted"\n'
        )

        from agents.trainer import tools as t

        monkeypatch.setattr(t, "_AGENTS_DIR", agents_root)
        monkeypatch.setitem(t._DIR_SLUG_BY_DB_NAME, "fake_agent", "fake_agent")

        out = t.read_agent_prompts("fake_agent")
        assert set(out.keys()) == {"NURTURE_SYSTEM", "NURTURE_USER"}
        assert out["NURTURE_SYSTEM"] == "Be concise. Use {persona}."
        assert out["NURTURE_USER"] == "Draft for {contact_name}."
        # lowercase / private must be excluded.
        assert "private_var" not in out
        assert "lowercase_const" not in out

    def test_returns_empty_on_missing_file(self, tmp_path, monkeypatch):
        from agents.trainer import tools as t

        monkeypatch.setattr(t, "_AGENTS_DIR", tmp_path / "nope")
        out = t.read_agent_prompts("ghost_agent")
        assert out == {}

    def test_returns_empty_on_syntax_error(self, tmp_path, monkeypatch):
        agents_root = tmp_path / "agents"
        fake = agents_root / "broken_agent"
        fake.mkdir(parents=True)
        (fake / "prompts.py").write_text("def malformed(\n  # never closes")

        from agents.trainer import tools as t

        monkeypatch.setattr(t, "_AGENTS_DIR", agents_root)
        monkeypatch.setitem(t._DIR_SLUG_BY_DB_NAME, "broken_agent", "broken_agent")

        out = t.read_agent_prompts("broken_agent")
        assert out == {}

    def test_does_not_execute_module(self, tmp_path, monkeypatch):
        """If the target prompts.py has a side-effecting top-level
        expression, AST parsing must NOT trigger it."""
        agents_root = tmp_path / "agents"
        fake = agents_root / "evil_agent"
        fake.mkdir(parents=True)
        # If this file is imported/executed, the assertion below fails.
        (fake / "prompts.py").write_text(
            "import os\n"
            "os.environ['TRAINER_EXEC_DETECTED'] = 'YES'\n"
            'GOOD_SYSTEM = "well-formed prompt"\n'
        )

        from agents.trainer import tools as t

        monkeypatch.delenv("TRAINER_EXEC_DETECTED", raising=False)
        monkeypatch.setattr(t, "_AGENTS_DIR", agents_root)
        monkeypatch.setitem(t._DIR_SLUG_BY_DB_NAME, "evil_agent", "evil_agent")

        out = t.read_agent_prompts("evil_agent")
        # The constant was extracted (file is AST-valid).
        assert out.get("GOOD_SYSTEM") == "well-formed prompt"
        # The side effect did NOT fire — we parsed, not executed.
        import os
        assert os.environ.get("TRAINER_EXEC_DETECTED") is None


# ─────────────────────────── deterministic guardrail ──────────────────


class TestValidateProposalText:
    """The three checks that gate every LLM proposal."""

    def test_accepts_prose_rewrite(self):
        from agents.trainer.tools import validate_proposal_text

        current = "Score the contact based on {persona} and {company_size}."
        proposed = (
            "Carefully evaluate the contact using their {persona} and "
            "{company_size}. Prioritize specific evidence."
        )
        ok, reason = validate_proposal_text(current, proposed)
        assert ok, f"unexpected rejection: {reason}"
        assert reason == ""

    def test_rejects_code_fences(self):
        from agents.trainer.tools import validate_proposal_text

        ok, reason = validate_proposal_text(
            "Score {x}.",
            "Score {x}.\n```python\nprint('hi')\n```",
        )
        assert not ok
        assert "code fence" in reason.lower()

    def test_rejects_class_definition(self):
        from agents.trainer.tools import validate_proposal_text

        ok, reason = validate_proposal_text(
            "Score {x}.",
            "class MyModel(BaseModel):\n    foo: int",
        )
        assert not ok
        assert "class definition" in reason.lower()

    def test_rejects_placeholder_added(self):
        from agents.trainer.tools import validate_proposal_text

        ok, reason = validate_proposal_text(
            "Score {persona}.",
            "Score {persona} based on {industry}.",  # added {industry}
        )
        assert not ok
        assert "placeholder" in reason.lower()

    def test_rejects_placeholder_removed(self):
        from agents.trainer.tools import validate_proposal_text

        ok, reason = validate_proposal_text(
            "Score {persona} and {industry}.",
            "Score {persona}.",  # removed {industry}
        )
        assert not ok
        assert "placeholder" in reason.lower()

    def test_rejects_placeholder_renamed(self):
        from agents.trainer.tools import validate_proposal_text

        ok, reason = validate_proposal_text(
            "Score {persona}.",
            "Score {persona_name}.",  # renamed
        )
        assert not ok
        assert "placeholder" in reason.lower()

    def test_accepts_no_placeholders_at_all(self):
        from agents.trainer.tools import validate_proposal_text

        ok, _ = validate_proposal_text(
            "Be concise and helpful.",
            "Be concise, helpful, and grounded in evidence.",
        )
        assert ok


# ─────────────────────────── identify_underperformers ─────────────────


class TestIdentifyUnderperformers:
    def _ap(self, **overrides):
        from agents.trainer.state import AgentPerformance

        defaults = dict(
            agent_name="test_agent",
            runs_total=100,
            runs_failure=2,
            failure_rate=0.02,
            hitl_approvals=10,
            hitl_rejections=1,
            rejection_ratio=1 / 11,
            total_cost_usd=10.0,
            p95_duration_ms=5000.0,
            median_cost_usd=0.05,
            p95_cost_usd=0.10,
            cost_variance_ratio=2.0,
            regression_flags=0,
        )
        defaults.update(overrides)
        return AgentPerformance(**defaults)

    def test_clean_agent_not_flagged(self):
        from agents.trainer.tools import identify_underperformers

        out = identify_underperformers([self._ap()])
        assert out == []

    def test_failure_rate_threshold(self):
        from agents.trainer.tools import identify_underperformers

        breaching = self._ap(
            agent_name="leaky", runs_total=50, runs_failure=10,
            failure_rate=0.20,
        )
        result = identify_underperformers([breaching])
        assert len(result) == 1
        assert result[0][0] == "leaky"
        assert "failure_rate" in result[0][1]

    def test_cost_variance_threshold(self):
        from agents.trainer.tools import identify_underperformers

        spiking = self._ap(
            agent_name="spiky",
            median_cost_usd=0.05,
            p95_cost_usd=0.50,
            cost_variance_ratio=10.0,
        )
        result = identify_underperformers([spiking])
        assert len(result) == 1
        assert "cost variance" in result[0][1].lower()

    def test_rejection_ratio_threshold(self):
        from agents.trainer.tools import identify_underperformers

        rejected = self._ap(
            agent_name="overruled",
            hitl_approvals=5, hitl_rejections=10,
            rejection_ratio=10 / 15,
        )
        result = identify_underperformers([rejected])
        assert len(result) == 1
        assert "overrul" in result[0][1].lower()

    def test_regression_flag(self):
        from agents.trainer.tools import identify_underperformers

        flagged = self._ap(agent_name="redalert", regression_flags=2)
        result = identify_underperformers([flagged])
        assert len(result) == 1
        assert "regression" in result[0][1].lower()

    def test_multiple_reasons_one_signal(self):
        """Agent breaches BOTH failure and cost thresholds → single
        underperformer entry with both reasons in the signal."""
        from agents.trainer.tools import identify_underperformers

        bad = self._ap(
            agent_name="double_trouble",
            runs_total=50, runs_failure=10, failure_rate=0.20,
            cost_variance_ratio=5.0, median_cost_usd=0.01, p95_cost_usd=0.05,
        )
        result = identify_underperformers([bad])
        assert len(result) == 1
        sig = result[0][1]
        assert "failure_rate" in sig
        assert "cost variance" in sig.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
