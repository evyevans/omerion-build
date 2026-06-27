"""One-shot OAuth consent flow to capture a personal-Gmail refresh token.

    python -m infra.google.oauth_setup

Prereq: create a Cloud project → OAuth consent screen (External, test
users include your own email) → Credentials → OAuth client ID
(Desktop) → download the client_secret_*.json file, then set:

    GOOGLE_OAUTH_CLIENT_ID     = <client id from that JSON>
    GOOGLE_OAUTH_CLIENT_SECRET = <client secret from that JSON>

Run this script; a browser tab opens; authorize the scopes; the script
prints the refresh token. Paste it into `.env` as:

    GOOGLE_OAUTH_REFRESH_TOKEN=1//0g...

Re-run only if scopes in `omerion_core.mcp.google_auth.SCOPES` change.
"""
from __future__ import annotations

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from omerion_core.mcp.google_auth import SCOPES
from omerion_core.settings import settings


def main() -> int:
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret):
        print(
            "ERROR: set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env first",
            file=sys.stderr,
        )
        return 1

    client_config = {
        "installed": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    print("\n──── Success ────")
    print("Paste this into .env as GOOGLE_OAUTH_REFRESH_TOKEN:")
    print()
    print(creds.refresh_token)
    print()
    print("Scopes granted:")
    print(json.dumps(list(creds.scopes or []), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
