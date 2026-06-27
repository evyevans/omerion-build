# Hermes Web Capability Tiers

Use the lowest tier that satisfies the task. Escalate only when the lower tier fails or cannot perform the action.

| Tier | Tools | Use when |
|------|-------|----------|
| 1 — Read | `web_search` (SerpAPI), `firecrawl_scrape` | Search results, static pages, article text |
| 2 — Single-page act | `firecrawl_scrape` then `firecrawl_interact` | Click/fill on one page after scrape |
| 3 — Research | `firecrawl_agent` | Autonomous multi-site research (Firecrawl-hosted) |
| 4 — Full browser | Playwright MCP (`@playwright/mcp`, headless) | Multi-step navigate/click/type/submit across a session |

## Rules

- Prefer Firecrawl for read-only extraction (faster, lower cost).
- Use `firecrawl_agent` before Playwright when the task is research, not UI automation.
- Use Playwright when the workflow requires persistent browser state across multiple pages or form steps.
- Do not log into third-party sites unless credentials are explicitly configured in `/data/.env`.
- On CAPTCHA or login walls: report `BLOCKED` and stop; do not retry blindly.

## Google Workspace (separate from web tiers)

- **Email send:** `email_send` → Omerion bridge (`HERMES_EMAIL_PROVIDER=omerion_bridge`). Never SMTP / app password.
- **Gmail read:** OAuth MCP tools (`gmail_search`, `gmail_read_message`).
- **Calendar / Docs / Drive write / Slides:** OAuth MCP tools.
- **Sheets / Drive read (shared folders):** Service account MCP tools.
