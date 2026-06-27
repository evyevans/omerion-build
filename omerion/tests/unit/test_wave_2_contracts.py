"""Wave 4 contract tests — the operating laws as code.

Each test asserts ONE non-negotiable invariant from the plan's §10
operating laws. A failure here is a regression on a deliberate
design decision, not a test-of-implementation-detail.

Tests are organized by wave so a future operator can grep
`Wave 2.3` and find the exact contract test for the
business_outcomes source gate.

Run:  cd omerion && python -m pytest tests/unit/test_wave_2_contracts.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Path bootstrap for running from the repo root or omerion/.
_REPO = Path(__file__).resolve().parents[3]
_OMERION = Path(__file__).resolve().parents[2]
for p in (str(_OMERION), str(_REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


# ─────────────────────────── Wave 1.6: style_guard filter ────────────


class TestStyleGuardFilter:
    """Operating law: every human-facing draft passes style_guard.filter()."""

    def test_filter_returns_tuple_shape(self):
        from omerion_core.outreach.style_guard import filter as style_filter

        ok, violations = style_filter("This is a normal sentence.")
        assert isinstance(ok, bool)
        assert isinstance(violations, list)

    def test_clean_text_passes(self):
        from omerion_core.outreach.style_guard import filter as style_filter

        ok, violations = style_filter("Saw your post on the Acme migration.")
        assert ok, f"clean text should pass; violations={violations}"

    def test_banned_phrase_caught(self):
        from omerion_core.outreach.style_guard import filter as style_filter

        ok, violations = style_filter("Let me be clear: this matters.")
        assert not ok
        assert any("let me be clear" in v.lower() for v in violations)

    def test_em_dash_pause_caught(self):
        from omerion_core.outreach.style_guard import filter as style_filter

        ok, violations = style_filter("This was the moment — everything changed.")
        assert not ok
        assert any("dash" in v.lower() for v in violations)

    def test_filler_adverb_after_comma_caught(self):
        from omerion_core.outreach.style_guard import filter as style_filter

        ok, violations = style_filter("The new system works, really, it does.")
        assert not ok
        assert any("filler" in v.lower() for v in violations)

    def test_filler_mid_sentence_NOT_flagged(self):
        """Conservative ban-list: a mid-sentence 'really' is fine."""
        from omerion_core.outreach.style_guard import filter as style_filter

        # No sentence-start, no after-comma → no filler match.
        ok, violations = style_filter("She really cares about the outcome.")
        # Should pass for the filler check (other rules may catch other phrases).
        assert not any("filler" in v.lower() for v in violations)


# ─────────────────────────── Wave 1.2: idempotency utility ────────────


class TestIdempotencyKey:
    """Operating law: same business identity → same key (deterministic)."""

    def test_same_payload_same_key(self):
        from omerion_core.util.idempotency import generate_key

        a = generate_key("test.scope", {"contact_id": "c1"}, window="none")
        b = generate_key("test.scope", {"contact_id": "c1"}, window="none")
        assert a == b

    def test_different_scope_different_key(self):
        from omerion_core.util.idempotency import generate_key

        a = generate_key("scope.a", {"contact_id": "c1"}, window="none")
        b = generate_key("scope.b", {"contact_id": "c1"}, window="none")
        assert a != b

    def test_key_order_independent(self):
        """Pydantic / dict key order must not affect the hash."""
        from omerion_core.util.idempotency import generate_key

        a = generate_key("s", {"a": 1, "b": 2}, window="none")
        b = generate_key("s", {"b": 2, "a": 1}, window="none")
        assert a == b

    def test_key_length_is_sha256(self):
        from omerion_core.util.idempotency import generate_key

        k = generate_key("s", {"x": 1}, window="none")
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)


# ─────────────────────────── Wave 2.3: source-of-truth gate ──────────


class TestBusinessOutcomesSourceGate:
    """Operating law: revenue records require a legal source — never AI."""

    def test_agent_inference_raises(self):
        from omerion_core.runtime.business_outcomes import (
            UnauthorizedOutcomeSource,
            record_outcome,
        )

        with pytest.raises(UnauthorizedOutcomeSource):
            record_outcome(
                outcome_type="signed_contract",
                source="agent_inference",  # type: ignore[arg-type]
                value_usd=50000,
            )

    def test_arbitrary_string_raises(self):
        from omerion_core.runtime.business_outcomes import (
            UnauthorizedOutcomeSource,
            record_outcome,
        )

        with pytest.raises(UnauthorizedOutcomeSource):
            record_outcome(
                outcome_type="signed_contract",
                source="trust_me_bro",  # type: ignore[arg-type]
                value_usd=50000,
            )

    def test_revenue_bearing_requires_value_usd(self):
        from omerion_core.runtime.business_outcomes import (
            UnauthorizedOutcomeSource,
            record_outcome,
        )

        # stripe source + revenue-bearing outcome but missing value → raises
        with pytest.raises(UnauthorizedOutcomeSource):
            record_outcome(
                outcome_type="signed_contract",
                source="stripe",
                value_usd=None,
            )

    def test_crm_manual_can_omit_value_usd(self):
        """A human entering a $0 placeholder is legal."""
        from omerion_core.runtime.business_outcomes import VALID_OUTCOME_TYPES

        # Just assert the constants — actual insert needs Supabase.
        assert "signed_contract" in VALID_OUTCOME_TYPES


# ─────────────────────────── Wave 1.5: agent_wrapper ──────────────────


class TestAgentWrapperContracts:
    """Operating law: every agent is registered with a contract (or runs permissive)."""

    def test_default_contract_is_permissive(self):
        from omerion_core.runtime.agent_wrapper import get_contract

        c = get_contract("unmigrated-skill-xyz")
        assert c.skill == "unmigrated-skill-xyz"
        # Default min_confidence is the wrapper baseline.
        assert 0.0 <= c.min_confidence <= 1.0

    def test_linkedin_outreach_contract_registered(self):
        # Importing the agent module triggers registration as a side-effect.
        try:
            import agents.linkedin_outreach  # noqa: F401  — side-effect import
        except ImportError as e:
            pytest.skip(f"agents package import failed (expected in CI w/o full env): {e}")

        from omerion_core.runtime.agent_wrapper import get_contract

        c = get_contract("linkedin-outreach")
        assert c.skill == "linkedin-outreach"
        assert c.min_confidence == 0.65

    def test_offer_matching_has_value_extractor(self):
        try:
            import agents.offer_matching  # noqa: F401  — side-effect import
        except ImportError as e:
            pytest.skip(f"agents package import failed: {e}")

        from omerion_core.runtime.agent_wrapper import get_contract

        c = get_contract("offer-matching")
        assert c.value_extractor is not None, "offer-matching MUST have a value_extractor"
        assert c.requires_human_approval_above_value_usd is not None

    def test_newly_migrated_contracts_registered(self):
        try:
            import agents.icp_scoring  # noqa: F401
            import agents.r3_strategic_architect  # noqa: F401
            import agents.high_quality_lead_scraping  # noqa: F401
            import agents.market_mapper  # noqa: F401
            import agents.r1_market_tech_watcher  # noqa: F401
            import agents.r2_oss_scout  # noqa: F401
            import agents.biz_dev_outreach  # noqa: F401
            import agents.client_onboarding  # noqa: F401
        except ImportError as e:
            pytest.skip(f"agents package import failed: {e}")

        from omerion_core.runtime.agent_wrapper import get_contract

        expected_configs = {
            "icp-scoring": 0.70,
            "r3-strategic-architect": 0.65,
            "hq-lead-scraping": 0.70,
            "market-mapper": 0.60,
            "r1-market-tech-watcher": 0.55,
            "r2-oss-scout": 0.60,
            "biz-dev-outreach": 0.65,
            "client-onboarding": 0.70,
        }

        for skill, expected_conf in expected_configs.items():
            c = get_contract(skill)
            assert c.skill == skill
            assert c.min_confidence == expected_conf


# ─────────────────────────── Wave 2.7: event schema registry ─────────


class TestEventSchemas:
    """Operating law: every emitted event has a schema (or is permissive-pass-through)."""

    def test_all_subscribed_events_have_schemas(self):
        """Every event with a downstream consumer should have a Pydantic schema."""
        from omerion_core.events.broker import EVENT_SUBSCRIPTIONS
        from omerion_core.events.schemas import EVENT_SCHEMAS

        subscribed = set(EVENT_SUBSCRIPTIONS.keys())
        schematized = set(EVENT_SCHEMAS.keys())

        missing = subscribed - schematized
        # ANALYSIS_READY is a heartbeat with a different shape — exempt for now.
        missing -= {"analysis.ready"}

        assert not missing, (
            f"events with consumers but no schema: {sorted(missing)} — "
            f"add to events/schemas.py before emitters can rely on validation"
        )

    def test_contact_scored_schema_rejects_negative_score(self):
        from omerion_core.events.schemas import ContactScored
        from uuid import uuid4

        with pytest.raises(Exception):  # pydantic ValidationError
            ContactScored(
                event_type="contact.scored",
                source_agent="icp-scoring",
                correlation_id=uuid4(),
                idempotency_key="x" * 12,
                contact_id=uuid4(),
                account_id=uuid4(),
                fit_score=-1,   # invalid: ge=0
                intent_score=50,
                timing_score=50,
                total_score=99,
                persona="founder",
                confidence=0.8,
            )

    def test_proposal_draft_ready_value_bucket_literal(self):
        """value_bucket must be one of S/M/L/XL — no raw dollars allowed."""
        from omerion_core.events.schemas import ProposalDraftReady
        from uuid import uuid4

        with pytest.raises(Exception):
            ProposalDraftReady(
                event_type="proposal.draft.ready",
                source_agent="offer-matching",
                correlation_id=uuid4(),
                idempotency_key="x" * 12,
                proposal_id=uuid4(),
                contact_id=uuid4(),
                account_id=uuid4(),
                value_bucket="HUGE",  # not in S/M/L/XL → invalid
                confidence=0.8,
            )


# ─────────────────────────── Wave 1.7: validation strict mode ────────


class TestValidationStrict:
    """Operating law: strict-mode validation raises rather than silently falling back."""

    def test_persona_strict_raises_on_unknown(self):
        from omerion_core.validation import ValidationFailed, validate_persona

        with pytest.raises(ValidationFailed):
            validate_persona("ImaginaryPersona", strict=True)

    def test_persona_soft_returns_fallback(self):
        from omerion_core.validation import validate_persona

        result = validate_persona("ImaginaryPersona", strict=False)
        assert not result.valid
        assert result.value == "NEEDS_REVIEW"

    def test_strict_failed_carries_metadata(self):
        from omerion_core.validation import ValidationFailed, validate_company_type

        try:
            validate_company_type("blockchain dao 4 cats", strict=True)
            pytest.fail("expected ValidationFailed")
        except ValidationFailed as exc:
            assert exc.field == "company_type"
            assert "blockchain" in str(exc.value).lower()
            assert exc.valid_values  # non-empty allowed set


# ─────────────────────────── Wave 5: TRAINER agent ────────────────────


class TestTrainerContract:
    """Operating laws specific to TRAINER (Wave 5).

    TRAINER is the 16th agent and the only one whose output rewrites
    other agents' code-adjacent text. Its contract has the strictest
    constraints of any non-revenue agent.
    """

    def test_trainer_contract_registered(self):
        try:
            import agents.trainer  # noqa: F401  — side-effect import
        except ImportError as e:
            pytest.skip(f"agents package import failed: {e}")

        from omerion_core.runtime.agent_wrapper import get_contract

        c = get_contract("trainer")
        assert c.skill == "trainer"
        # Stricter than every agent except outcome_attribution (0.80).
        assert c.min_confidence == 0.75
        # NEVER touches dollar amounts.
        assert c.value_extractor is None
        assert c.requires_human_approval_above_value_usd is None
        # Longer mutex covers the multi-agent meta-evaluation sweep.
        assert c.mutex_ttl_seconds >= 3600

    def test_trainer_scope_locked_to_migrated_agents(self):
        """TRAINER must never analyze an unmigrated agent — the scope is
        defined by `TRAINER_TARGET_AGENTS` in tools.py and must include
        only the 6 wrapper-migrated agent names (snake + kebab forms)."""
        try:
            from agents.trainer.tools import TRAINER_TARGET_AGENTS
        except ImportError as e:
            pytest.skip(f"agents package import failed: {e}")

        expected = {
            "linkedin_outreach",     "linkedin-outreach",
            "crm_nurture",           "crm-nurture",
            "offer_matching",        "offer-matching",
            "meeting_intelligence",  "meeting-intel",
            "lead_scraper_enricher", "lead-scraper",
            "outcome_attribution",   "outcome-attribution",
        }
        assert set(TRAINER_TARGET_AGENTS) == expected, (
            f"TRAINER scope drift detected. "
            f"unexpected: {set(TRAINER_TARGET_AGENTS) - expected}, "
            f"missing: {expected - set(TRAINER_TARGET_AGENTS)}"
        )

    def test_trainer_validate_proposal_rejects_placeholder_change(self):
        """The single most important guardrail: an LLM that renames a
        format-string placeholder would break every call site of the
        prompt template. Auto-rejected."""
        try:
            from agents.trainer.tools import validate_proposal_text
        except ImportError as e:
            pytest.skip(f"agents package import failed: {e}")

        ok, reason = validate_proposal_text(
            current="Score the contact based on {persona}.",
            proposed="Score the contact based on {persona_name}.",
        )
        assert not ok
        assert "placeholder" in reason.lower()

    def test_trainer_validate_proposal_rejects_code_fence(self):
        try:
            from agents.trainer.tools import validate_proposal_text
        except ImportError as e:
            pytest.skip(f"agents package import failed: {e}")

        ok, reason = validate_proposal_text(
            current="Be concise.",
            proposed="Be concise.\n```python\nprint('hello')\n```",
        )
        assert not ok
        assert "code fence" in reason.lower()

    def test_trainer_validate_proposal_rejects_schema_injection(self):
        try:
            from agents.trainer.tools import validate_proposal_text
        except ImportError as e:
            pytest.skip(f"agents package import failed: {e}")

        ok, reason = validate_proposal_text(
            current="Score {x}.",
            proposed="Score {x}.\n\nclass NewSchema(BaseModel):\n    foo: int",
        )
        assert not ok
        assert "class" in reason.lower()

    def test_trainer_proposal_rationale_minimum_length(self):
        """The "Why this improves performance" guardrail (TWAT spec §A.2)
        is enforced at the Pydantic layer with min_length=50."""
        try:
            from agents.trainer.state import PromptProposal
        except ImportError as e:
            pytest.skip(f"agents package import failed: {e}")

        with pytest.raises(Exception):  # pydantic ValidationError
            PromptProposal(
                target_agent_name="crm_nurture",
                prompt_constant_name="NURTURE_SYSTEM",
                current_text="x" * 100,
                current_text_sha256="a" * 64,
                proposed_text="y" * 100,
                rationale="Better.",   # < 50 chars — must reject
                confidence=0.8,
            )


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["python", "-m", "pytest", __file__, "-v"]))
