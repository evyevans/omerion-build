"""Unit tests for HEALER event schemas and broker wiring."""
import uuid

import pytest

from omerion_core.events.bus import EventType
from omerion_core.events.schemas import EVENT_SCHEMAS, parse_event


def test_regression_alert_schema_registered():
    assert "regression.alert" in EVENT_SCHEMAS


def test_healing_applied_schema_registered():
    assert "healing.applied" in EVENT_SCHEMAS


def test_regression_alert_round_trips():
    payload = {
        "source_agent": "r4_regression_alert",
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": "test-key-1234",
        "failing_agent": "crm_nurture",
        "severity": "high",
        "metric": "error_rate",
        "metric_value": 0.45,
        "alert_run_id": str(uuid.uuid4()),
    }
    evt = parse_event("regression.alert", payload)
    assert evt.failing_agent == "crm_nurture"
    assert evt.severity == "high"


def test_healing_applied_round_trips():
    payload = {
        "source_agent": "healer",
        "correlation_id": str(uuid.uuid4()),
        "idempotency_key": "test-key-5678",
        "healed_agent": "crm_nurture",
        "remediation_type": "config_patch",
        "audit_id": str(uuid.uuid4()),
        "fix_applied": True,
    }
    evt = parse_event("healing.applied", payload)
    assert evt.healed_agent == "crm_nurture"
    assert evt.fix_applied is True


def test_healing_applied_in_event_type_enum():
    assert EventType.HEALING_APPLIED.value == "healing.applied"


def test_broker_wires_healer():
    from omerion_core.events.broker import EVENT_SUBSCRIPTIONS
    assert "healer" in EVENT_SUBSCRIPTIONS.get("regression.alert", [])


def test_broker_wires_auditor_on_healing():
    from omerion_core.events.broker import EVENT_SUBSCRIPTIONS
    assert "auditor" in EVENT_SUBSCRIPTIONS.get("healing.applied", [])
