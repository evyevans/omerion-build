# Vault — Omerion Agentic Workflow Factory

This is a cross-project pointer file. When opened from inside the Obsidian
vault by Claude Code, it briefs the assistant on the project context.

## What lives here

This vault is the founder's working notebook. Agents in the factory write to
it under these areas:

- `research/` — Notes from SEEKER, STRATEGIST briefs, ANALYST dossiers.
- `_hot/` — Hot-cache files. Always-fresh snapshots that overwrite on update
  (current ICP, current offer set, this week's three actions, etc.).
- `meetings/` — SCRIBE-produced meeting briefs (action items, decisions).
- `decisions/` — Founder decisions surfaced from approval flows.
- `outbound/` — Drafts of OUTREACH and NURTURE messages awaiting review.
- `clients/<slug>/` — Per-client notes synced from `clients/<slug>/`.
- `rsi/` — RSI proposals, weekly synthesis output, applied patches log.

## Conventions Claude should follow when editing this vault

- Each note is a Markdown file. Use H1 (`#`) for the title; never more than
  one H1 per file.
- YAML frontmatter is preferred over inline metadata. Required keys when
  present: `source_url`, `ingested_at`, `agent`, `topic`.
- Cross-links use `[[wikilinks]]`, not relative paths.
- Never delete files in `_hot/` directly. They are overwritten by the
  `update_hot_cache` writer; manual edits are lost on next agent write.

## Where the factory lives

The factory code is at the project root, not inside the vault. The vault is
written-to by `obsidian/sync/vault_writer.py`. To run the factory:

```
uv run uvicorn main:app --reload
```

To trigger autoresearch into this vault:

```
POST /api/v1/agents/seeker/run
{ "focus": "..." }
```

The output lands under `research/<date>-<slug>.md`.
