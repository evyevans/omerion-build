"""Tests for client_intake EventType entries and broker subscriptions."""
from omerion_core.events.bus import EventType


def test_client_intake_event_types_exist():
    assert hasattr(EventType, "CLIENT_PROFILE_READY")
    assert EventType.CLIENT_PROFILE_READY.value == "client.profile.ready"
    assert hasattr(EventType, "CLIENT_INTAKE_GAPS_DETECTED")
    assert EventType.CLIENT_INTAKE_GAPS_DETECTED.value == "client.intake.gaps_detected"


def test_client_intake_events_in_broker():
    from omerion_core.events.broker import EVENT_SUBSCRIPTIONS
    assert EventType.CLIENT_PROFILE_READY.value in EVENT_SUBSCRIPTIONS
    assert EventType.CLIENT_INTAKE_GAPS_DETECTED.value in EVENT_SUBSCRIPTIONS
