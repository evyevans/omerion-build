"""Tests for HEALER agent — state, tools guardrail, and graph construction."""
from agents.healer.state import HealerState


def test_default_state():
    s = HealerState(
        session_id="test-session",
        failing_agent="crm_nurture",
        severity="high",
        metric="error_rate",
        metric_value=0.45,
    )
    assert s.agent_name == "healer"
    assert s.diagnosis_attempts == 0
    assert s.fix_applied is False
    assert s.remediation_type is None


def test_critical_requires_hitl():
    s = HealerState(
        session_id="s",
        failing_agent="crm_nurture",
        severity="critical",
        metric="cost_usd",
        metric_value=9.99,
        diagnosis_confidence=0.5,
        diagnosis_attempts=2,
    )
    assert s.requires_hitl_escalation is True


def test_non_critical_no_hitl():
    s = HealerState(
        session_id="s",
        failing_agent="crm_nurture",
        severity="high",
        metric="error_rate",
        metric_value=0.3,
        diagnosis_confidence=0.85,
        diagnosis_attempts=1,
    )
    assert s.requires_hitl_escalation is False


def test_critical_but_high_confidence_no_hitl():
    s = HealerState(
        session_id="s",
        failing_agent="crm_nurture",
        severity="critical",
        metric="error_rate",
        metric_value=0.9,
        diagnosis_confidence=0.85,
        diagnosis_attempts=2,
    )
    assert s.requires_hitl_escalation is False


def test_critical_low_confidence_first_attempt_no_hitl():
    """Only escalates after 2 attempts, not on the first."""
    s = HealerState(
        session_id="s",
        failing_agent="crm_nurture",
        severity="critical",
        metric="error_rate",
        metric_value=0.9,
        diagnosis_confidence=0.3,
        diagnosis_attempts=1,
    )
    assert s.requires_hitl_escalation is False


# ── Tools guardrail ───────────────────────────────────────────────────────────

from agents.healer.tools import ALLOWED_EXTENSIONS, validate_target_resource
import pytest


def test_validate_blocks_py_files():
    with pytest.raises(ValueError, match="CORE_LOGIC_MUTATION"):
        validate_target_resource("omerion_core/events/broker.py")


def test_validate_blocks_py_in_agents():
    with pytest.raises(ValueError, match="CORE_LOGIC_MUTATION"):
        validate_target_resource("agents/crm_nurture/graph.py")


def test_validate_allows_yaml():
    validate_target_resource("config/agents.yaml")  # must not raise


def test_validate_allows_skill_md():
    validate_target_resource("skills/crm-nurture.skill.md")  # must not raise


def test_validate_blocks_unknown_extension():
    with pytest.raises(ValueError):
        validate_target_resource("config/something.json")


# ── Loop guard ───────────────────────────────────────────────────────────────

def test_loop_guard_triggers_on_third_fix():
    s = HealerState(
        session_id="s",
        failing_agent="crm_nurture",
        severity="high",
        metric="error_rate",
        metric_value=0.5,
        recent_fix_count=2,
    )
    assert s.loop_guard_active is True


def test_loop_guard_off_on_first_fix():
    s = HealerState(
        session_id="s",
        failing_agent="crm_nurture",
        severity="high",
        metric="error_rate",
        metric_value=0.5,
        recent_fix_count=0,
    )
    assert s.loop_guard_active is False


# ── RAG context ───────────────────────────────────────────────────────────────

def test_load_rag_context_returns_list():
    from unittest.mock import MagicMock, patch
    from agents.healer.tools import load_rag_context

    mock_index = MagicMock()
    mock_index.query.return_value = MagicMock(
        matches=[
            MagicMock(
                score=0.92,
                metadata={"text": "crm_nurture backoff was set to 60s after latency spike in 2026-04"},
            ),
        ]
    )
    with patch("agents.healer.tools.pinecone_index", return_value=mock_index), \
         patch("omerion_core.llm.embeddings.embed", return_value=[0.0] * 512):
        results = load_rag_context("crm_nurture", "backoff_seconds")
    assert isinstance(results, list)
    assert len(results) == 1
    assert "backoff" in results[0]


# ── YAML patching ─────────────────────────────────────────────────────────────

def test_yaml_patch_raises_on_nonexistent_key():
    import pytest
    from agents.healer.tools import patch_yaml_config

    with pytest.raises(KeyError):
        patch_yaml_config("agents.nonexistent_agent.nonexistent_key", 999)


# ── Graph construction ────────────────────────────────────────────────────────

def test_graph_builds():
    from agents.healer.graph import build
    graph = build()
    assert graph is not None
    assert hasattr(graph, "invoke")


# ── Regression: critical bug fixes + gate-every-patch (G3) ────────────────────

from unittest.mock import patch as _patch
from uuid import uuid4 as _uuid4

import agents.healer.graph as _H

_HP = "agents.healer.graph."


def _hstate(**kw):
    base = dict(run_id=_uuid4(), session_id=str(_uuid4()), failing_agent="crm_nurture",
                severity="high", metric="error_rate", metric_value=0.4)
    base.update(kw)
    return HealerState(**base)


def test_diagnose_parses_router_dict_text():
    """REGRESSION: Tier.STANDARD didn't exist + json.loads ran on the router DICT."""
    from omerion_core.llm.router import Tier
    s = _hstate()
    resp = {"text": '{"root_cause":"x","confidence":0.9,"recommended_remediation":"config_patch"}',
            "cost_usd": 0.0}
    with _patch.object(_H, "_llm") as llm, \
         _patch(_HP + "load_agent_telemetry", return_value=[]), \
         _patch(_HP + "load_error_samples", return_value=[]), \
         _patch(_HP + "load_recent_runs", return_value=[]), \
         _patch(_HP + "load_config_section", return_value={}), \
         _patch(_HP + "load_rag_context", return_value=[]):
        llm.complete.return_value = resp
        out = _H.diagnose_root_cause(s)
    assert out["root_cause"] == "x" and out["diagnosis_confidence"] == 0.9
    assert llm.complete.call_args.kwargs["tier"] == Tier.DEFAULT   # not the broken STANDARD


def test_needs_hitl_gates_every_patch():
    assert _H._needs_hitl(_hstate(remediation_type="config_patch",
                                  target_resource="config/agents.yaml")) == "hitl_review"
    assert _H._needs_hitl(_hstate(remediation_type="prompt_update",
                                  target_resource="skills/crm-nurture.skill.md")) == "hitl_review"
    assert _H._needs_hitl(_hstate(remediation_type="escalated")) == "apply_fix"


def test_hitl_wait_extracts_decision():
    s = _hstate(); s.review_id = _uuid4()
    with _patch(_HP + "interrupt", return_value={"decisions": {str(s.review_id): "rejected"}}):
        out = _H.hitl_wait(s)
    assert out["hitl_decision"] == "rejected"


def test_apply_fix_blocks_rejected_patch():
    """The gate must actually block: a rejected patch writes nothing."""
    s = _hstate(remediation_type="config_patch", target_resource="config/agents.yaml",
                hitl_decision="rejected")
    with _patch(_HP + "validate_target_resource") as v, _patch(_HP + "backup_file") as b, \
         _patch(_HP + "patch_yaml_config") as pc:
        out = _H.apply_fix(s)
    v.assert_not_called(); b.assert_not_called(); pc.assert_not_called()
    assert out["fix_applied"] is False
