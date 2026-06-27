"""VaultWriter — writes structured agent output into an Obsidian vault.

The vault is filesystem-only: each page is a Markdown file under
`<vault>/<area>/`. We do not touch Obsidian's internal indexes; Obsidian
re-indexes on next open.

Methods:
  write_page(area, title, body, frontmatter=None) -> Path
  append_to_log(area, line)                       -> Path
  update_hot_cache(name, content)                 -> Path
  ingest_source(area, source_url, content)        -> Path
  run_autoresearch(topic)                         -> list[Path]
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

from core.settings import settings

log = logging.getLogger("omerion.obsidian")

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(s: str) -> str:
    return _SLUG_RE.sub("-", s.strip())[:80].strip("-").lower() or "untitled"


class VaultError(RuntimeError):
    pass


class VaultWriter:
    def __init__(self, vault_path: str | None = None) -> None:
        path = vault_path or settings.obsidian_vault_path
        if not path:
            raise VaultError("OBSIDIAN_VAULT_PATH not configured")
        self.root = Path(path).expanduser().resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)

    def _area_dir(self, area: str) -> Path:
        d = self.root / _slug(area)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _frontmatter(meta: dict[str, Any] | None) -> str:
        if not meta:
            return ""
        return "---\n" + yaml.safe_dump(meta, sort_keys=False).rstrip() + "\n---\n\n"

    def write_page(self, area: str, title: str, body: str,
                   frontmatter: dict[str, Any] | None = None) -> Path:
        d = self._area_dir(area)
        fname = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{_slug(title)}.md"
        path = d / fname
        path.write_text(self._frontmatter(frontmatter) + f"# {title}\n\n{body}\n",
                        encoding="utf-8")
        return path

    def append_to_log(self, area: str, line: str) -> Path:
        d = self._area_dir(area)
        path = d / "log.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- `{ts}` {line}\n")
        return path

    def update_hot_cache(self, name: str, content: str) -> Path:
        d = self._area_dir("_hot")
        path = d / f"{_slug(name)}.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        path.write_text(f"<!-- updated {ts} -->\n\n{content}\n", encoding="utf-8")
        return path

    async def ingest_source(self, area: str, source_url: str,
                             content: str | None = None) -> Path:
        if content is None:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                r = await c.get(source_url, headers={"User-Agent": "OmerionVault/1.0"})
                r.raise_for_status()
                content = r.text
        title = source_url.rstrip("/").split("/")[-1] or "source"
        return self.write_page(
            area, title, content,
            frontmatter={"source_url": source_url,
                          "ingested_at": datetime.now(timezone.utc).isoformat()},
        )

    async def run_autoresearch(self, topic: str) -> list[Path]:
        """Calls the factory's SEEKER agent (if reachable) and writes its
        finds into the vault. Falls back to a placeholder if Seeker can't
        be invoked from this environment."""
        try:
            from core.runtime.config_loader import load_resolved_config
            from core.schemas.base import TenantContext
            from core.agents.agent_registry import get_registry
            client_slug = settings.default_client_slug
            cfg = load_resolved_config(client_slug)
            ctx = TenantContext(
                client_slug=client_slug,
                industry_pack=cfg.industry_pack.name,
                departments_enabled=cfg.departments_enabled,
                agents_enabled=cfg.agents_enabled,
                integrations=cfg.integrations,
            )
            seeker = get_registry().get("seeker")
            out = await seeker.run(ctx, {"focus": topic})
            finds = (out.result or {}).get("finds", [])
        except Exception as e:
            log.warning("autoresearch_failed", extra={"err": str(e)})
            finds = []
        written: list[Path] = []
        for f in finds:
            p = self.write_page(
                "research",
                f.get("name") or topic,
                "## What\n"
                f"{f.get('what','')}\n\n"
                "## Why I care\n"
                f"{f.get('why_care','')}\n\n"
                "## Ship it\n"
                f"{f.get('ship_it','')}\n\n"
                "## Gotcha\n"
                f"{f.get('gotcha','')}\n",
                frontmatter={"topic": topic, "agent": "seeker"},
            )
            written.append(p)
        if not written:
            written.append(self.write_page(
                "research", f"autoresearch-{topic}",
                f"No finds produced for topic: **{topic}**\n",
                frontmatter={"topic": topic, "status": "empty"},
            ))
        return written
