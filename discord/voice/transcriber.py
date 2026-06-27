"""Whisper transcription for Discord voice messages.

Two paths:
  1. OPENAI_API_KEY set → use OpenAI's hosted Whisper (`whisper-1`).
     This is the production default — async, no local GPU needed.
  2. Local fallback via `faster-whisper` if installed and configured.
     Useful for air-gapped dev.

Returns plain text. Raises TranscriptionError on failure."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from core.settings import settings

log = logging.getLogger("omerion.voice.transcriber")


class TranscriptionError(RuntimeError):
    pass


async def transcribe_url(audio_url: str) -> str:
    """Download an audio attachment from Discord and transcribe it."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(audio_url)
        if r.status_code >= 400:
            raise TranscriptionError(f"download failed: {r.status_code}")
        content = r.content
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(content)
        path = Path(f.name)
    try:
        return await transcribe_file(path)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


async def transcribe_file(path: Path) -> str:
    api_key = os.getenv("OPENAI_API_KEY") or settings.openai_api_key
    if api_key:
        return await _openai_whisper(path, api_key)
    return _faster_whisper(path)


async def _openai_whisper(path: Path, api_key: str) -> str:
    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=180) as c:
        with path.open("rb") as f:
            files = {"file": (path.name, f, "application/octet-stream")}
            data = {"model": "whisper-1"}
            r = await c.post(url, headers=headers, files=files, data=data)
        if r.status_code >= 400:
            raise TranscriptionError(f"whisper api {r.status_code}: {r.text[:200]}")
        try:
            return (r.json().get("text") or "").strip()
        except ValueError:
            return r.text.strip()


def _faster_whisper(path: Path) -> str:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise TranscriptionError(
            "OPENAI_API_KEY not set and faster-whisper not installed"
        ) from e
    model = WhisperModel(settings.whisper_model, compute_type="int8")
    segments, _info = model.transcribe(str(path), beam_size=1)
    return " ".join(seg.text.strip() for seg in segments).strip()
