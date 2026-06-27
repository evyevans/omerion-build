# Hermes VPS — A.R.I.E.S prompt pack

Send these to A.R.I.E.S in Telegram **in order**. Omerion repo changes (Phase 2) are already applied locally.

## 1. Functional tests (Phase 1)

Copy entire file → send to A.R.I.E.S:

`PHASE1_FUNCTIONAL_TESTS.txt`

Expected: 9/9 PASS report with raw tool output.

## 2. Slides MCP (Phase 3)

After Phase 1 passes, send:

`ARIES_SLIDES_MCP_PROMPT.txt`

Expected: slides_create + slides_read working, presentation URL returned.

## 3. Playwright browser (Phase 4 + verify)

After Slides, send:

`ARIES_PLAYWRIGHT_MCP_PROMPT.txt`

Also ask A.R.I.E.S to ingest `WEB_CAPABILITY_TIERS.md` into Hermes skill/memory (paste file contents or upload).

Expected: Playwright probes A–C pass, tier model stored.

## Omerion Railway (Phase 2 — done in repo)

Code updated:
- `omerion_core/mcp/google_auth.py` — full scopes; refresh without scope override
- `omerion_core/clients/google_client.py` — `slides_service()`

**Deploy:** push to main / trigger Railway redeploy so production runs the scope fix.

Local + Railway send-email verified after changes (200 OK).
