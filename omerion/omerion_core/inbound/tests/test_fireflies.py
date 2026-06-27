"""TestClient coverage for /webhooks/fireflies."""
from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FIREFLIES_WEBHOOK_SECRET", "ff-secret")
    from omerion_core.settings import settings
    monkeypatch.setattr(settings, "fireflies_webhook_secret", "ff-secret")
    from omerion_core.inbound.app import app
    return TestClient(app)


def _sign(body: bytes, secret: str = "ff-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_missing_signature_rejected(client):
    resp = client.post("/webhooks/fireflies", data=b"{}")
    assert resp.status_code == 401


def test_wrong_signature_rejected(client):
    resp = client.post("/webhooks/fireflies", data=b"{}", headers={"x-fireflies-signature": "sha256=deadbeef"})
    assert resp.status_code == 401


def test_non_transcript_event_ignored(client):
    body = json.dumps({"event": "meeting.scheduled", "meetingId": "m-1"}).encode()
    resp = client.post("/webhooks/fireflies", data=body, headers={"x-fireflies-signature": _sign(body)})
    assert resp.status_code == 200
    assert resp.json()["thread_id"] is None


def test_transcript_completed_starts_thread(client):
    body = json.dumps({"event": "transcript.completed", "meetingId": "m-42"}).encode()
    handler = SimpleNamespace(handler=MagicMock(return_value={"ok": True}))
    with patch("omerion_core.inbound.fireflies.get_handler", return_value=handler) as gh:
        resp = client.post(
            "/webhooks/fireflies",
            data=body,
            headers={"x-fireflies-signature": _sign(body)},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["meeting_id"] == "m-42"
    assert data["thread_id"].startswith("meeting:m-42:")
    gh.assert_called_once_with("meeting-intelligence")
    handler.handler.assert_called_once()


def test_missing_meeting_id_400(client):
    body = json.dumps({"event": "transcript.completed"}).encode()
    resp = client.post("/webhooks/fireflies", data=body, headers={"x-fireflies-signature": _sign(body)})
    assert resp.status_code == 400


def test_fireflies_client_uses_async_httpx():
    """transcript() must be a coroutine (uses httpx.AsyncClient, not Client)."""
    import inspect
    from omerion_core.clients.fireflies_client import FirefliesClient
    c = FirefliesClient("dummy-key")
    assert inspect.iscoroutinefunction(c.transcript), (
        "FirefliesClient.transcript must be async def — sync httpx.Client blocks the event loop"
    )
