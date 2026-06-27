"""TestClient coverage for /hitl/resolve — local runtime (post-gateway)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OMERION_WEBHOOK_TOKEN", "unit-test-token")
    from omerion_core.settings import settings
    monkeypatch.setattr(settings, "omerion_webhook_token", "unit-test-token")
    from omerion_core.inbound.app import app
    return TestClient(app)


def _bearer():
    return {"Authorization": "Bearer unit-test-token"}


def test_rejects_missing_bearer(client):
    resp = client.post("/hitl/resolve", json={"review_id": "x", "token": "y", "decision": "approved"})
    assert resp.status_code == 401


def test_rejects_wrong_bearer(client):
    resp = client.post(
        "/hitl/resolve",
        json={"review_id": "x", "token": "y", "decision": "approved"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_returns_404_when_review_missing(client):
    with patch("omerion_core.inbound.hitl.get_review", return_value=None):
        resp = client.post(
            "/hitl/resolve",
            json={"review_id": "rev-1", "token": "t", "decision": "approved"},
            headers=_bearer(),
        )
    assert resp.status_code == 404


def test_resolve_and_resume_happy_path(client):
    review_row = {"review_id": "rev-1", "session_id": "thread-1", "decision": "pending"}
    resolved = {"review_id": "rev-1", "decision": "approved", "correlation_id": "c-1"}
    with patch("omerion_core.inbound.hitl.get_review", return_value=review_row), \
         patch("omerion_core.inbound.hitl.resolve_review", return_value=resolved) as resolver, \
         patch("omerion_core.inbound.hitl.resume_thread", return_value={"ok": True}) as resumer:
        resp = client.post(
            "/hitl/resolve",
            json={"review_id": "rev-1", "token": "t", "decision": "approved", "decided_by": "founder@omerion.io"},
            headers=_bearer(),
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"review_id": "rev-1", "decision": "approved", "thread_resumed": True, "correlation_id": "c-1"}
    resolver.assert_called_once()
    resumer.assert_called_once()


def test_bad_token_returns_401(client):
    with patch("omerion_core.inbound.hitl.get_review", return_value={"review_id": "rev-1", "session_id": None}), \
         patch("omerion_core.inbound.hitl.resolve_review", side_effect=PermissionError("invalid HITL token")):
        resp = client.post(
            "/hitl/resolve",
            json={"review_id": "rev-1", "token": "bad", "decision": "approved"},
            headers=_bearer(),
        )
    assert resp.status_code == 401


def test_resume_failure_does_not_rollback_decision(client):
    review_row = {"review_id": "rev-1", "session_id": "thread-1"}
    resolved = {"review_id": "rev-1", "decision": "rejected", "correlation_id": "c-1"}
    with patch("omerion_core.inbound.hitl.get_review", return_value=review_row), \
         patch("omerion_core.inbound.hitl.resolve_review", return_value=resolved), \
         patch("omerion_core.inbound.hitl.resume_thread", side_effect=RuntimeError("checkpoint unavailable")):
        resp = client.post(
            "/hitl/resolve",
            json={"review_id": "rev-1", "token": "t", "decision": "rejected"},
            headers=_bearer(),
        )
    assert resp.status_code == 200
    assert resp.json()["thread_resumed"] is False


def test_edited_decision_passes_new_body_through(client):
    review_row = {"review_id": "rev-1", "session_id": "thread-1"}
    resolved = {"review_id": "rev-1", "decision": "approved", "correlation_id": "c-1"}
    with patch("omerion_core.inbound.hitl.get_review", return_value=review_row), \
         patch("omerion_core.inbound.hitl.resolve_review", return_value=resolved), \
         patch("omerion_core.inbound.hitl.resume_thread", return_value={"ok": True}) as resumer:
        resp = client.post(
            "/hitl/resolve",
            json={
                "review_id": "rev-1",
                "token": "t",
                "decision": "edited",
                "new_body": "revised copy",
                "source_channel": "discord",
            },
            headers=_bearer(),
        )
    assert resp.status_code == 200
    payload = resumer.call_args.kwargs["resume_payload"]
    assert payload["decision"] == "edited"
    assert payload["new_body"] == "revised copy"
    assert payload["source_channel"] == "discord"
