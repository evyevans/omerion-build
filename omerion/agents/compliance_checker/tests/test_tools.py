"""Tests for COMPLIANCE_CHECKER deterministic rules."""
from agents.compliance_checker.tools import _APPROVED_HOSTS
from agents.compliance_checker.state import ComplianceViolation


def test_approved_hosts_frozenset_immutable():
    """Whitelist must be a frozenset — immutable at runtime."""
    assert isinstance(_APPROVED_HOSTS, frozenset)
    assert "api.anthropic.com" in _APPROVED_HOSTS


def test_approved_hosts_does_not_contain_unknown():
    assert "evil.example.com" not in _APPROVED_HOSTS


def test_compliance_violation_model():
    v = ComplianceViolation(
        rule_id="CC-1:COST_CAP",
        severity="critical",
        target_agent="crm_nurture",
        description="exceeded cap",
    )
    assert v.severity == "critical"
    assert v.rule_id == "CC-1:COST_CAP"
    assert v.target_agent == "crm_nurture"


def test_compliance_violation_optional_agent():
    v = ComplianceViolation(
        rule_id="CC-2:DATA_RETENTION",
        severity="warning",
        description="old records",
    )
    assert v.target_agent is None
