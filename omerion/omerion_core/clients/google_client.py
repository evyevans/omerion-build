"""Google Workspace clients — Gmail, Calendar, Drive, Sheets, Docs, Slides.

Uses OAuth refresh-token credentials (see `mcp.google_auth`). Personal
Gmail doesn't support service-account domain-wide delegation, so the
whole Python surface goes through the user's own OAuth grant.

These thin wrappers remain during the MCP transition so that existing
callers (`crm_nurture.tools`, `infra.google.bootstrap_sheet`) keep
working while Agent-SDK agents migrate to the MCP Workspace server.
"""
from __future__ import annotations

from functools import lru_cache

import gspread
from googleapiclient.discovery import Resource, build

from omerion_core.mcp.google_auth import google_credentials


@lru_cache(maxsize=1)
def gmail_service() -> Resource:
    return build("gmail", "v1", credentials=google_credentials(), cache_discovery=False)


@lru_cache(maxsize=1)
def calendar_service() -> Resource:
    return build("calendar", "v3", credentials=google_credentials(), cache_discovery=False)


@lru_cache(maxsize=1)
def drive_service() -> Resource:
    return build("drive", "v3", credentials=google_credentials(), cache_discovery=False)


@lru_cache(maxsize=1)
def sheets_client() -> gspread.Client:
    return gspread.authorize(google_credentials())


@lru_cache(maxsize=1)
def sheets_service() -> Resource:
    """Raw Sheets v4 API — use for batchUpdate / add-sheet operations."""
    return build("sheets", "v4", credentials=google_credentials(), cache_discovery=False)


@lru_cache(maxsize=1)
def docs_service() -> Resource:
    """Google Docs API — used by Build Orchestrator client-mode (Phase 4)."""
    return build("docs", "v1", credentials=google_credentials(), cache_discovery=False)


@lru_cache(maxsize=1)
def slides_service() -> Resource:
    """Google Slides API — create/read presentations."""
    return build("slides", "v1", credentials=google_credentials(), cache_discovery=False)
