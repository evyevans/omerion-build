"""
Shared Google Sheets API v4 client — service account authentication.
All other tools import from here; no tool instantiates its own client.
"""
import os
import json
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_CREDS_PATH = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "secrets/service_account.json")
_SPREADSHEET_ID = os.environ.get("COMMAND_CENTER_SHEET_ID", "")

_service = None


def _get_service():
    global _service
    if _service is None:
        creds = service_account.Credentials.from_service_account_file(
            _CREDS_PATH, scopes=SCOPES
        )
        _service = build("sheets", "v4", credentials=creds)
    return _service


def read_range(sheet_id: str, range_name: str) -> list[list[Any]]:
    result = (
        _get_service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=range_name)
        .execute()
    )
    return result.get("values", [])


def append_row(sheet_id: str, range_name: str, values: list[Any]) -> None:
    body = {"values": [values]}
    _get_service().spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body=body,
    ).execute()


def update_cell(sheet_id: str, range_name: str, value: Any) -> None:
    body = {"values": [[value]]}
    _get_service().spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()


def batch_update(sheet_id: str, data: list[dict]) -> None:
    """data: list of {"range": "Sheet!A1", "values": [[...]]}"""
    body = {"valueInputOption": "USER_ENTERED", "data": data}
    _get_service().spreadsheets().values().batchUpdate(
        spreadsheetId=sheet_id, body=body
    ).execute()


def rows_to_dicts(raw: list[list[Any]]) -> list[dict[str, Any]]:
    """Convert a header-row + data grid into a list of dicts."""
    if not raw or len(raw) < 2:
        return []
    headers = raw[0]
    return [
        {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        for row in raw[1:]
    ]
