"""Async HTTP client for the Omerion FastAPI control plane.

Every method attaches the bearer token, enforces a timeout, and raises
BotAPIError on non-2xx responses so callers handle one exception type.
"""
from __future__ import annotations

from typing import Any

import httpx


class BotAPIError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        super().__init__(f"HTTP {status}: {body[:200]}")


class OmerionClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get(self, path: str, **params: Any) -> Any:
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as c:
            r = await c.get(f"{self._base}{path}", params={k: v for k, v in params.items() if v is not None})
            if r.is_error:
                raise BotAPIError(r.status_code, r.text)
            return r.json()

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(headers=self._headers, timeout=self._timeout) as c:
            r = await c.post(f"{self._base}{path}", json=body or {})
            if r.is_error:
                raise BotAPIError(r.status_code, r.text)
            return r.json()

    # ── Agent lifecycle ───────────────────────────────────────────────────────

    async def run_agent(
        self,
        name: str,
        *,
        inputs: dict | None = None,
        source_channel: str = "discord",
        discord_channel_id: str | None = None,
        discord_thread_id: str | None = None,
        triggered_by: str | None = None,
    ) -> dict:
        return await self._post(f"/agents/{name}/run", {
            "inputs": inputs,
            "source_channel": source_channel,
            "discord_channel_id": discord_channel_id,
            "discord_thread_id": discord_thread_id,
            "triggered_by": triggered_by,
        })

    async def get_run(self, run_id: str) -> dict:
        return await self._get(f"/agents/runs/{run_id}")

    async def list_runs(
        self,
        agent_name: str | None = None,
        status_filter: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        return await self._get("/agents/runs", agent_name=agent_name, status_filter=status_filter, limit=limit)

    async def pause_agent(self, name: str) -> dict:
        return await self._post(f"/agents/{name}/pause")

    async def resume_agent(self, name: str) -> dict:
        return await self._post(f"/agents/{name}/resume")

    async def cancel_session(self, session_id: str) -> dict:
        return await self._post(f"/agents/sessions/{session_id}/cancel")

    # ── HITL ──────────────────────────────────────────────────────────────────

    async def get_pending_hitl(self) -> list[dict]:
        return await self._get("/hitl/pending")

    async def resolve_hitl(
        self,
        review_id: str,
        token: str,
        decision: str,
        new_body: str | None = None,
    ) -> dict:
        return await self._post("/hitl/resolve", {
            "review_id": review_id,
            "token": token,
            "decision": decision,
            "source_channel": "discord",
            "new_body": new_body,
        })

    # ── Inbound routing ───────────────────────────────────────────────────────

    async def route_discord_message(
        self,
        channel_name: str,
        guild_id: str,
        author: str,
        message: str,
        discord_channel_id: str | None = None,
        discord_thread_id: str | None = None,
    ) -> dict:
        return await self._post("/inbound/discord/route", {
            "channel_name": channel_name,
            "guild_id": guild_id,
            "author": author,
            "message": message,
            "discord_channel_id": discord_channel_id,
            "discord_thread_id": discord_thread_id,
        })

    async def route_discord_voice(
        self,
        channel_name: str,
        guild_id: str,
        author: str,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        discord_channel_id: str | None = None,
        discord_thread_id: str | None = None,
    ) -> dict:
        """POST a voice-memo attachment to the Whisper transcription endpoint.

        Uses a longer timeout than the default because Whisper transcription
        adds several seconds of round-trip latency on top of routing.
        """
        files = {"audio_file": (filename, audio_bytes, content_type)}
        data = {
            "channel_name": channel_name,
            "guild_id": guild_id,
            "author": author,
            "discord_channel_id": discord_channel_id,
            "discord_thread_id": discord_thread_id,
        }
        data = {k: v for k, v in data.items() if v is not None}
        async with httpx.AsyncClient(headers=self._headers, timeout=60.0) as c:
            r = await c.post(f"{self._base}/inbound/discord/voice", data=data, files=files)
            if r.is_error:
                raise BotAPIError(r.status_code, r.text)
            return r.json()

    # ── Reports ───────────────────────────────────────────────────────────────

    async def get_daily_report(self) -> dict:
        return await self._get("/reports/daily")

    async def get_status_rollup(self) -> dict:
        return await self._get("/reports/status")

    async def get_cost_report(self) -> dict:
        return await self._get("/reports/costs")

    async def get_pipeline_snapshot(self) -> dict:
        return await self._get("/reports/pipeline")

    async def get_mission_control(self) -> dict:
        return await self._get("/mission-control")
