"""ElevenLabs client — async voice synthesis (used by ORIA-adjacent tooling)."""
from __future__ import annotations

from functools import lru_cache

from elevenlabs.client import AsyncElevenLabs

from omerion_core.settings import settings


@lru_cache(maxsize=1)
def elevenlabs_client() -> AsyncElevenLabs:
    if not settings.elevenlabs_api_key:
        raise RuntimeError("ELEVENLABS_API_KEY must be set")
    return AsyncElevenLabs(api_key=settings.elevenlabs_api_key)
