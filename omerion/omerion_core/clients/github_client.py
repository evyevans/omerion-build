"""GitHub client — used by Build Orchestrator (#9)."""
from __future__ import annotations

from functools import lru_cache

from github import Auth, Github

from omerion_core.settings import settings


@lru_cache(maxsize=1)
def github_client() -> Github:
    if not settings.github_token:
        raise RuntimeError("GITHUB_TOKEN must be set")
    return Github(auth=Auth.Token(settings.github_token))
