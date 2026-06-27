"""Personal-Gmail OAuth — refresh-token flow.

Personal Google accounts do not support service-account domain-wide
delegation, so we use the "installed app" OAuth pattern instead:

  1. User runs the consent flow once locally (e.g. `python -m
     infra.google.oauth_setup`) which captures a long-lived refresh token.
  2. Everything thereafter uses that refresh token to mint short-lived
     access tokens on demand.

The three values live in settings as GOOGLE_OAUTH_CLIENT_ID / SECRET /
REFRESH_TOKEN.
"""
from __future__ import annotations

from functools import lru_cache

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from omerion_core.settings import settings

# Full Workspace surface the agents need. Scope creep here invalidates the
# existing refresh token — consent flow must be re-run if you add scopes.
SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/presentations",
]

_TOKEN_URL = "https://oauth2.googleapis.com/token"


@lru_cache(maxsize=1)
def google_credentials() -> Credentials:
    """Build Credentials from the stored refresh token. Auto-refreshes access tokens."""
    if not (
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.google_oauth_refresh_token
    ):
        raise RuntimeError(
            "Google OAuth not configured. Set GOOGLE_OAUTH_CLIENT_ID, "
            "GOOGLE_OAUTH_CLIENT_SECRET, and GOOGLE_OAUTH_REFRESH_TOKEN "
            "(run `python -m infra.google.oauth_setup` once to capture)."
        )

    creds = Credentials(
        token=None,
        refresh_token=settings.google_oauth_refresh_token,
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        token_uri=_TOKEN_URL,
        # Omit scopes= — the refresh token carries granted scopes; passing a
        # list here causes invalid_scope if it doesn't match exactly (see A.R.I.E.S
        # VPS pre-flight). SCOPES above is for oauth_setup consent only.
    )
    creds.refresh(Request())
    return creds
