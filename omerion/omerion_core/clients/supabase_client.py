"""Supabase client — service-role, bypasses RLS, used by all agents."""
from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from omerion_core.settings import settings


@lru_cache(maxsize=1)
def _client() -> Client:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


class _SupabaseProxy:
    """Lazy proxy so that importing doesn't require env vars at import time."""

    def __getattr__(self, name: str):
        return getattr(_client(), name)


supabase = _SupabaseProxy()
