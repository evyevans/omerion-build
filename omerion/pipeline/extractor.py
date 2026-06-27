"""FileExtractor — pulls plain text from Google Drive files.

Supported MIME types:
  application/vnd.google-apps.document     → Drive export as text/plain
  application/vnd.google-apps.spreadsheet  → Sheets API (all tabs as TSV)
  application/pdf                          → pdfplumber
  application/vnd.openxmlformats-officedocument.wordprocessingml.document → python-docx
  text/plain, text/markdown                → direct download
  everything else                          → returns None (silently skipped)
"""
from __future__ import annotations

import io
import json
import re
from functools import lru_cache
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("pipeline.extractor")

_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

GDOC_MIME = "application/vnd.google-apps.document"
GSHEET_MIME = "application/vnd.google-apps.spreadsheet"
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown"}


@lru_cache(maxsize=1)
def _drive_service():
    """Lazy singleton — service account credentials from settings."""
    sa_json = settings.google_service_account_json
    if not sa_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON must be set (path to JSON file)")
    try:
        with open(sa_json) as f:
            info = json.load(f)
        creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot load service account JSON from {sa_json}: {exc}") from exc
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@lru_cache(maxsize=1)
def _sheets_service():
    """Lazy singleton for Sheets API v4."""
    sa_json = settings.google_service_account_json
    if not sa_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON must be set (path to JSON file)")
    with open(sa_json) as f:
        info = json.load(f)
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _clean(text: str) -> str:
    """Strip null bytes, collapse 3+ blank lines to 2, trim edges."""
    text = text.replace("\x00", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _download_bytes(file_id: str) -> bytes:
    service = _drive_service()
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


class FileExtractor:
    """Extract plain UTF-8 text from a Google Drive file."""

    def extract(self, file_id: str, mime_type: str) -> Optional[str]:
        """Return plain text for supported types, None for unsupported."""
        try:
            if mime_type == GDOC_MIME:
                return self._export_gdoc(file_id)
            if mime_type == GSHEET_MIME:
                return self._extract_gsheet(file_id)
            if mime_type == PDF_MIME:
                return self._extract_pdf(file_id)
            if mime_type == DOCX_MIME:
                return self._extract_docx(file_id)
            if mime_type in TEXT_MIMES:
                raw = _download_bytes(file_id)
                return _clean(raw.decode("utf-8", errors="replace"))
            log.warning("extractor_unsupported_mime", file_id=file_id, mime_type=mime_type)
            return None
        except Exception as exc:  # noqa: BLE001
            log.error("extractor_failed", file_id=file_id, mime_type=mime_type, error=str(exc))
            return None

    def _export_gdoc(self, file_id: str) -> str:
        service = _drive_service()
        resp = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        text = resp if isinstance(resp, str) else resp.decode("utf-8", errors="replace")
        return _clean(text)

    def _extract_gsheet(self, file_id: str) -> str:
        """Extract all tabs from a Google Sheet as structured text.

        Each tab is rendered as:
          [Sheet: Tab Name]
          col1\tcol2\tcol3
          val1\tval2\tval3
          ...
        Tabs that are empty are skipped.
        """
        svc = _sheets_service()

        # 1. Discover all sheet tab titles
        meta = svc.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties(title,sheetId,sheetType)"
        ).execute()
        tabs = [
            s["properties"]["title"]
            for s in meta.get("sheets", [])
            if s["properties"].get("sheetType", "GRID") == "GRID"
        ]

        if not tabs:
            log.warning("gsheet_no_grid_tabs", file_id=file_id)
            return ""

        # 2. Batch-read all tabs in a single API call
        ranges = [f"'{tab}'" for tab in tabs]
        batch = (
            svc.spreadsheets()
            .values()
            .batchGet(spreadsheetId=file_id, ranges=ranges, valueRenderOption="FORMATTED_VALUE")
            .execute()
        )

        sections: list[str] = []
        for value_range in batch.get("valueRanges", []):
            rows: list[list[str]] = value_range.get("values", [])
            if not rows:
                continue
            # Normalise row widths
            width = max(len(r) for r in rows)
            lines = ["\t".join(r + [""] * (width - len(r))) for r in rows]
            tab_name = value_range.get("range", "").split("!")[0].strip("'")
            sections.append(f"[Sheet: {tab_name}]\n" + "\n".join(lines))

        log.info("gsheet_extracted", file_id=file_id, tabs=len(sections))
        return _clean("\n\n".join(sections))

    def _extract_pdf(self, file_id: str) -> str:
        import pdfplumber  # optional dep — only imported if needed

        raw = _download_bytes(file_id)
        pages: list[str] = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                pages.append(page_text)
        return _clean("\n\n".join(pages))

    def _extract_docx(self, file_id: str) -> str:
        import docx  # python-docx — only imported if needed

        raw = _download_bytes(file_id)
        doc = docx.Document(io.BytesIO(raw))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return _clean("\n\n".join(paragraphs))
