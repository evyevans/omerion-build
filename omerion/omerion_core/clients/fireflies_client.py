"""Fireflies.ai GraphQL client — transcripts for Meeting Intelligence Agent."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import httpx

from omerion_core.settings import settings

FIREFLIES_API = "https://api.fireflies.ai/graphql"


class FirefliesClient:
    def __init__(self, api_key: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(
                FIREFLIES_API,
                json={"query": query, "variables": variables or {}},
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                raise RuntimeError(f"Fireflies API error: {data['errors']}")
            return data["data"]

    async def transcript(self, meeting_id: str) -> dict[str, Any]:
        query = """
        query Transcript($id: String!) {
          transcript(id: $id) {
            id title date duration host_email participants
            sentences { speaker_name text start_time }
            summary { overview action_items keywords outline }
          }
        }
        """
        return (await self._post(query, {"id": meeting_id}))["transcript"]


@lru_cache(maxsize=1)
def fireflies_client() -> FirefliesClient:
    if not settings.fireflies_api_key:
        raise RuntimeError("FIREFLIES_API_KEY must be set")
    return FirefliesClient(settings.fireflies_api_key)
