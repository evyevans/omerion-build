---
name: biz-dev-outreach
tier: A
agent_number: 15
graph: agents.biz_dev_outreach.graph:build
schedule: "0 9 * * 1,3,5"   # Mon/Wed/Fri 09:00 America/Toronto
triggers:
  - cron
  - webhook:discord.biz
events_consumed: []
events_emitted:                    # emitted by THIS graph's emit node only:
  - job.posting.discovered         #   JOB_POSTING_DISCOVERED — per ranked posting
  - job.application.sent           #   APPLICATION_SENT — per approved+submitted draft
  - job.application.ghosted        #   APPLICATION_GHOSTED — per stale (>ghost_threshold) app
  # NOTE: job.application.drafted / job.application.responded are real EventTypes
  # but are NOT emitted by this graph (responded is owned by the reply-tracker).
hitl: true
discord_channel: biz
rate_limits:
  - firecrawl
  - gmail
  - anthropic
  - openai
concurrency:
  lock: pg_advisory_lock
  key: agent_name
---

# SEEK (Job Hunter)

SEEK is the inbound-revenue counterpart to REACH and NURTURE: instead of
warming an existing book, it finds *new* consulting work for Evykynn Panton
across freelance platforms, growth-stage startup boards, and automation-focused
employer ATS feeds — then drafts tailored applications under strict guardrails
and routes the batch through founder HITL before any submission reaches the wire.

The agent must NEVER apply unilaterally, NEVER fabricate experience, NEVER
submit identical cover letters to two postings, and NEVER touch a posting
that hits a scam-signal or forbidden-keyword filter.

---

## TRIGGER

- **Schedule:** `0 9 * * 1,3,5` in `America/Toronto` (Mon / Wed / Fri at 09:00).
  Three runs per week is intentional — it batches founder review into
  predictable windows and respects the `daily_application_cap: 3` rate budget.
- **Manual:** post `seek run` in Discord channel `#seek` → routes through
  `omerion_core/inbound/discord_route.py` (`CHANNEL_SKILL_MAP["seek"]`).
- **Event triggers:** none currently. SEEK is purely cron / manual.
- **DO NOT run when:** the founder has an open SEEK HITL review pending
  (the advisory lock on `agent_name` blocks parallel runs).

---

## WORKFLOW

The graph executes 11 nodes in order. Each step lists its purpose, inputs,
output, failure mode, and the tool function backing it. The compiled
`StateGraph` lives in [graph.py](../agents/job_seeker/graph.py).

```
discover_postings
  → filter_relevant
  → load_profile
  → rank_opportunities
  → draft_applications
  → flag_risks
  → hitl_review
  → hitl_wait               ← interrupt(); PostgresSaver checkpoints here
  → submit_applications
  → track_status
  → emit
```

### 1. `discover_postings`
- **Purpose:** fan out to every configured source, tier-S → A → B in order.
- **Input:** `agents.yaml job_seeker.sources.*` config blocks.
- **Output:** `state.raw_postings` (deduped against `job_postings` table by
  `(platform, external_id)`).
- **Tools called:** `fetch_toptal_rss`, `fetch_ateam_rss`,
  `fetch_braintrust_rss`, `fetch_contra_rss`, `fetch_wellfound_jobs`,
  `fetch_yc_jobs`, `fetch_lever_board`, `fetch_greenhouse_board`,
  `fetch_upwork_rss`, `fetch_indeed_rss`, `fetch_linkedin_jobs`,
  `dedup_postings`.
- **Failure mode:** any individual fetcher that errors logs a warning and
  returns `[]`. The run continues with whatever sources succeeded — never
  aborts the batch.

### 2. `filter_relevant`
- **Purpose:** drop postings whose embedded text doesn't cosine-match the
  founder profile vector.
- **Input:** `state.raw_postings`, embedded `state.resume_text`.
- **Output:** `state.relevant_postings` (only those scoring
  `>= state.min_relevance_score`, default `0.65`).
- **Tools called:** `embed_profile`, `score_postings`.
- **Failure mode:** if Pinecone is down, individual postings get
  `relevance_score = 0.0` and are filtered out — the batch shrinks but
  doesn't fail.

### 3. `load_profile`
- **Purpose:** read `assets/evykynn/resume.md` and `assets/evykynn/cover_letter.md`
  into state for use by the drafter.
- **Tools called:** `load_resume`, `load_cover_letter_template`.
- **Failure mode:** missing files are logged as warnings; the rest of the
  graph runs with empty strings (drafts will be lower quality but the run
  doesn't crash).

### 4. `rank_opportunities`
- **Purpose:** apply the weighted RANK_SYSTEM rubric (40% domain, 25% stack,
  15% budget, 10% remote, 10% engagement length) and zero-out scam postings.
- **Input:** `state.relevant_postings`.
- **Output:** `state.ranked_postings` (top N opportunities with
  `rank_score >= state.min_rank_score`, sorted desc).
- **Tools called:** `ClaudeRouter.complete(tier=Tier.FAST)` with `RANK_SYSTEM`
  → JSON parse → assigns `posting.rank_score` and `posting.rank_rationale`.
- **Failure mode:** if the LLM call fails or JSON parse fails, falls back
  to deterministic `relevance_score * budget_bonus` ranking. Logged as
  `seek_rank_llm_failed_fallback`.

### 5. `draft_applications`
- **Purpose:** generate one tailored application per ranked opportunity.
- **Input:** `state.ranked_postings`, `state.resume_text`, `state.cover_letter_template`.
- **Output:** `state.drafts` (excluding any whose `cover_letter_body == "SKIP"` —
  the model emits `SKIP` per `SEEK_SYSTEM` guardrail rule 4 when a posting
  fails the apply-floor checks).
- **Tools called:** `draft_application` (Sonnet via `ClaudeRouter`).
- **Failure mode:** per-posting errors are caught, logged
  (`seek_draft_error`), and recorded into `state.errors` — the rest of the
  batch continues.

### 6. `flag_risks`
- **Purpose:** deterministic HITL watchlist. For every draft, run all 10
  flag checks and attach `draft.hitl_flags` + `draft.hitl_notes` so the
  founder review card surfaces them inline.
- **Tools called:** `flag_application_risks`.
- **Output:** `state.drafts_with_flags` (count for the review header).
- **Failure mode:** pure function — no external calls — cannot fail.

### 7. `hitl_review`
- **Purpose:** build the markdown review card and create a row in
  `founder_review_queue` with the Discord approve/reject buttons.
- **Tools called:** `create_founder_review_task` from
  `omerion_core/hitl/review.py`.
- **Output:** `state.review_id`.
- **The card's anatomy:** `REVIEW_CONTEXT_HEADER` block (counts + watchlist
  legend) → one section per draft with rank score, flag list, posting URL,
  cover letter, optional Upwork proposal or outreach message.

### 8. `hitl_wait`
- **Purpose:** suspend the graph at a `langgraph.types.interrupt(...)`.
  PostgresSaver checkpoints the entire `SeekState` here — the run can sleep
  for hours/days waiting for the founder's Discord button click without
  consuming any compute.
- **Output:** `state.decision` ∈ `{"approved", "rejected"}` plus optional
  `state.scratch["decision_notes"]`.

### 9. `submit_applications`
- **Purpose:** if approved, persist + actually send.
- **Per-draft sequence:**
  1. `upsert_posting(posting)` → `job_postings` row
  2. `upsert_application(draft, run_id, review_id)` → `job_applications` row
     with `hitl_flags` JSON column populated
  3. `index_posting_pinecone(posting)` → vector in `job_postings` namespace
  4. **Submission:**
     - `platform == "upwork"` → `queue_upwork_application(draft)` (status
       `queued_for_sender`; Upwork API integration pending)
     - all other platforms → `send_application_email(draft, posting)`
       resolves recipient via `_extract_recipient_email(posting)` and falls
       back to `omerion.io@gmail.com` only with a logged warning.
- **Failure mode:** delivery failures are caught per draft, logged, and
  recorded; `state.submitted_count` reflects only confirmed sends.

### 10. `track_status`
- **Purpose:** flag prior applications past `ghost_threshold_days` (default
  14) for the ghost event emission.
- **Tools called:** `check_ghost_applications(threshold_days)`.

### 11. `emit`
- **Purpose:** publish lifecycle events on the bus.
- **Events:** `JOB_POSTING_DISCOVERED` per ranked posting,
  `APPLICATION_SENT` per approved draft, `APPLICATION_GHOSTED` per stale row.

---

## AGENT

### Persona
The drafter writes as **Evykynn Panton — AI Automation Consultant**.
The full positioning lives in [resume.md](../assets/evykynn/resume.md) and
[cover_letter.md](../assets/evykynn/cover_letter.md); the prompt loads
both verbatim every run, with no truncation. Voice is *confident, direct,
results-focused* — peer-to-peer with senior operators, never supplicant.

### Model tiering (via `ClaudeRouter`)
| Stage              | Tier            | Why                                                |
|--------------------|-----------------|----------------------------------------------------|
| `rank_opportunities` | `Tier.FAST` (Haiku)   | Structured JSON over many candidates; speed > nuance |
| `draft_applications` | `Tier.DEFAULT` (Sonnet) | Tailored prose; needs reasoning over resume + posting |
| (reserved) outreach_target classification edge-cases | `Tier.DEEP` (Opus) | Used only when LinkedIn person/job ambiguity is high |

### Prompts inventory ([prompts.py](../agents/job_seeker/prompts.py))
| Constant                 | Purpose                                                 |
|--------------------------|---------------------------------------------------------|
| `SEEK_SYSTEM`            | Persona + 8 hard guardrails + success criteria          |
| `APPLICATION_USER`       | Per-posting context + strict 4-section output schema    |
| `RANK_SYSTEM` / `_USER`  | Weighted rubric + auto-skip rules + JSON output format  |
| `HITL_FLAG_SYSTEM` / `_USER` | (reserved for LLM-augmented flagging; current `flag_risks` node is deterministic) |
| `REVIEW_CONTEXT_HEADER`  | The Discord card preamble + flag-meaning watchlist      |

### GUARDRAILS — the 8 commandments
Enforced in `SEEK_SYSTEM`. Violations of #1, #2, #6, or #7 are also detected
deterministically by `flag_risks`.

1. **NEVER** invent work history, tenure, dates, certifications, employers,
   or projects not in `resume.md`.
2. **NEVER** reuse identical cover-letter text across two postings — each
   draft must reference at least one specific detail from THIS posting.
3. **NEVER** pad with generic praise of the company.
4. **NEVER** apply to postings with `$0`/undefined budget AND vague
   description AND `< 200 chars` — emit `SKIP`.
5. **NEVER** use sycophantic openers ("thrilled", "excited", "passionate"),
   exclamation marks, emojis, or "synergy"/"leverage" as a verb.
6. **NEVER** name internal Omerion codenames (DAAM/CAPA/REMI/ASAP/OMERION)
   in cover letters — refer to them functionally only.
7. **NEVER** claim performance numbers unless verbatim in `resume.md`.
8. **NEVER** apply to a different role family (W2 senior eng IC,
   customer-success, recruiter, sales SDR) — emit `SKIP`.

### SUCCESS CRITERIA — what a fulfilled run looks like
- ≥ 1 posting per Tier-S/A source discovered, all deduped against history
- All drafts have `rank_score >= 7.0` OR carry the `low_rank_score` flag
- Founder receives Discord card within 60 s of `hitl_review` node entry
- On approve, applications submitted within 5 min; provider IDs persisted
- Daily application cap (3) respected
- Langfuse trace prefix `seek.*` populated for every node

### FAILURE MODES — what gets flagged for HITL
The 10 flag strings emitted by `flag_application_risks`:
| Flag                    | Trigger                                                |
|-------------------------|--------------------------------------------------------|
| `low_rank_score`        | `draft.rank_score < flag_thresholds.low_rank_score`    |
| `missing_budget`        | `posting.budget_low is None and budget_high is None`   |
| `scam_signal`           | description matches `_SCAM_PATTERNS` regex             |
| `skill_mismatch`        | required_skills present and ≥ half not in resume       |
| `short_deadline`        | `application_deadline` within `short_deadline_days`    |
| `duplicate_company`     | same company applied to in `duplicate_company_days`    |
| `forbidden_keyword`     | company or description matches `forbidden_company_keywords` |
| `identical_cover_text`  | Jaccard overlap with another draft > threshold (0.70)  |
| `vague_scope`           | description `< 300 chars`                              |
| `off_brand_voice`       | draft contains banned tokens (codenames, emoji, "!")   |

---

## TOOLS

All tool functions live in [tools.py](../agents/job_seeker/tools.py). They
are grouped by family below. Every fetcher returns `list[JobPosting]` and
fail-soft: on error they log a warning and return `[]`, never raise.

### Tier S — invite-only / curated freelance networks

#### `fetch_toptal_rss(feeds: list[str]) -> list[JobPosting]`
Toptal is invite-only; postings reach the top tier of freelancers.
Highest-bid client work for AI consulting in NA.
- **Source:** `https://www.toptal.com/freelance-jobs/feed?category=ai-engineer`
- **Rate limit:** 1 req / 2 s per feed
- **Implementation:** delegates to `_fetch_generic_rss(feeds, "toptal", ...)`

#### `fetch_ateam_rss(feeds: list[str]) -> list[JobPosting]`
A.Team curated mission marketplace for senior product builders. Quarterly+
engagements, premium rates.
- **Source:** `https://a.team/missions/feed?category=ai-ml`

#### `fetch_braintrust_rss(feeds: list[str]) -> list[JobPosting]`
User-owned freelance network; lower platform-fee leakage than Upwork.
- **Source:** `https://app.usebraintrust.com/jobs/feed?role=ai-engineer`

#### `fetch_contra_rss(feeds: list[str]) -> list[JobPosting]`
Commission-free independent platform.
- **Source:** `https://contra.com/explore/feed?category=ai`

### Tier A — high-signal startup / automation-focused employer boards

#### `fetch_wellfound_jobs(search_urls: list[str], api_key: str) -> list[JobPosting]`
Wellfound (formerly AngelList Talent) — startup-direct postings via
Firecrawl scrape (no Wellfound API for free tier).
- **Source:** `https://wellfound.com/jobs?keywords=...`
- **Auth:** Firecrawl API key from `settings.firecrawl_api_key`
- **Rate limit:** 1 s between Firecrawl calls

#### `fetch_yc_jobs(search_urls: list[str], api_key: str) -> list[JobPosting]`
YC Work-at-a-Startup — high-signal because postings come from funded YC
companies; many are early-stage AI/automation startups.
- **Source:** `https://www.workatastartup.com/jobs?role=ml&jobType=contract`

#### `fetch_lever_board(company_slugs: list[str]) -> list[JobPosting]`
Lever-hosted ATS boards. **Public JSON API, no scraping, no auth.**
- **API:** `https://api.lever.co/v0/postings/{slug}?mode=json`
- **Configured slugs:** `linear`, `make`
- **Code skeleton:**
  ```python
  resp = httpx.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=15.0)
  for job in resp.json():
      yield JobPosting(
          platform="lever",
          external_id=str(job["id"]),
          title=job["text"],
          company=slug.replace("-", " ").title(),
          description=_strip_html(job.get("descriptionPlain") or job["description"])[:3000],
          url=job["hostedUrl"],
          posted_at=_epoch_ms_to_iso(job.get("createdAt")),
      )
  ```

#### `fetch_greenhouse_board(company_slugs: list[str]) -> list[JobPosting]`
Greenhouse-hosted ATS boards. **Public JSON API, no scraping, no auth.**
- **API:** `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`
- **Configured slugs:** `notion`, `zapier`, `retool`, `airtable`

### Tier B — volume freelance + general boards

#### `fetch_upwork_rss(feeds: list[str]) -> list[JobPosting]`
Upwork public RSS feeds keyed by search query.
- **Budget parser:** `_parse_upwork_budget(summary)` handles `$50-$75/hr`,
  `$50 to $75/hr`, `$50–75 per hour`, `Budget: $1,500`, `Less than $500`,
  `Est. budget: $5,000`.

#### `fetch_indeed_rss(feeds: list[str]) -> list[JobPosting]`
Indeed public RSS. Detects remote from title/summary keyword scan
(Indeed RSS doesn't expose a remote attribute reliably).

#### `fetch_linkedin_jobs(search_urls, api_key) -> list[JobPosting]`
LinkedIn Jobs scraped via Firecrawl. Returns mixed postings + `outreach_target`
records when `_looks_like_person()` heuristic matches a person row instead
of a job row.

### Profile + scoring

#### `load_resume() -> str` / `load_cover_letter_template() -> str`
Read `omerion/assets/evykynn/{resume,cover_letter}.md`. Warn-and-empty if
missing.

#### `embed_profile(resume_text, cover_letter) -> list[float]`
Embeds the combined founder text into Pinecone namespace `job_postings`
under fixed ID `evykynn_profile`. Used as the cosine reference for
`score_postings`.

#### `score_postings(postings, profile_vector) -> list[JobPosting]`
Dot-product cosine similarity (OpenAI vectors are unit-normalized) between
each posting and the founder profile. Sets `posting.relevance_score` ∈ [0,1].

#### `index_posting_pinecone(posting) -> str`
Persists each posting vector with full metadata (platform, posting_id,
budget, rank_score, relevance_score, content_date, source_url) in the
`job_postings` namespace. Vector ID format: `posting:{platform}:{external_id}`.

### Drafting + risk

#### `draft_application(router, posting, resume_text, cover_letter_template) -> ApplicationDraft`
Single LLM call (`Tier.DEFAULT`, max_tokens=1200, temperature=0.4) using
`SEEK_SYSTEM` + formatted `APPLICATION_USER`. Returns a draft with the four
sections parsed via `_extract_section`. **No truncation** of resume/cover
letter — Sonnet's 200k context handles full assets.

#### `flag_application_risks(draft, posting, prior_drafts, forbidden_company_keywords, flag_thresholds) -> tuple[list[str], str]`
Pure function. Runs all 10 flag checks deterministically — no LLM, no
embedded API calls (DB hit only for `_company_recently_applied`, which
fail-opens to `False`).

### Persistence + send

#### `dedup_postings(raw) -> list[JobPosting]`
Filters against existing `(platform, external_id)` rows in `job_postings`.
Fail-open: if the SELECT errors, returns all postings (no false-rejects).

#### `upsert_posting(posting) -> str`
Idempotent UPSERT on `(platform, external_id)`. Persists the new
`application_deadline`, `required_skills`, `rank_score`, `rank_rationale`
columns from migration 0019.

#### `upsert_application(draft, run_id, review_id) -> str`
Idempotent UPSERT on `(posting_id, resume_version)`. Persists `hitl_flags`
JSON + `hitl_notes` text from migration 0019.

#### `send_application_email(draft, posting) -> str`
Recipient resolution order:
1. `_extract_recipient_email(posting)` — parses first plausible email from
   description / company / target_title (filters out `noreply`,
   `unsubscribe`, etc.)
2. Founder inbox `omerion.io@gmail.com` — last-resort, logged warning.

Sends via `omerion_core.clients.google_client.gmail_service()`, then updates
`job_applications.status = 'sent' / submitted_at / provider_ref`.

#### `queue_upwork_application(draft) -> None`
Sets `status = queued_for_sender`. Manual submission required until Upwork
API integration lands.

### Lifecycle

#### `check_ghost_applications(threshold_days: int = 14) -> list[dict]`
Returns `job_applications` rows with `status='sent'` and
`submitted_at < now - threshold_days` and no `replied_at`.

---

## SOURCES — at a glance

### Tier S — invite-only / curated (highest hourly rates)
- Toptal — `toptal.com/freelance-jobs/feed`
- A.Team — `a.team/missions/feed`
- Braintrust — `app.usebraintrust.com/jobs/feed`
- Contra — `contra.com/explore/feed`

### Tier A — high-signal startup / automation-focused employer boards
- Wellfound — `wellfound.com/jobs?keywords=...`
- YC Work-at-a-Startup — `workatastartup.com/jobs`
- Lever boards: Linear, Make
- Greenhouse boards: Notion, Zapier, Retool, Airtable

### Tier B — volume freelance + general boards
- Upwork RSS (4 query feeds)
- Indeed RSS (3 query feeds)
- LinkedIn Jobs via Firecrawl (3 search URLs)

### Cold-outreach person sources (kind="outreach_target")
- LinkedIn Sales Navigator queries via Firecrawl
- TechCrunch / VentureBeat founder/CEO interviews
- Product Hunt maker profiles

---

## CONFIG REFERENCE

All runtime config in [agents.yaml](../config/agents.yaml) under `job_seeker:`.

| Field | Purpose |
|---|---|
| `target_platforms` | Whitelist of platform IDs SEEK is allowed to ingest |
| `max_postings_per_platform` | Per-platform cap before dedup (default 15) |
| `min_relevance_score` | Pinecone cosine threshold to leave `filter_relevant` |
| `min_rank_score` | LLM rubric threshold to leave `rank_opportunities` |
| `top_n_to_draft` | Cap on drafts per run (default 5) |
| `daily_application_cap` | Hard ceiling on submitted applications per day |
| `ghost_threshold_days` | Days after `submitted_at` before flagging as ghosted |
| `forbidden_company_keywords` | Auto-skip + flag list |
| `flag_thresholds.*` | Per-flag tunables (low_rank_score, deadline_days, etc.) |
| `sources.tier_s_invite_only.*` | Tier-S RSS feed URLs |
| `sources.tier_a_high_signal.*` | Tier-A search URLs + ATS slugs |
| `sources.tier_b_volume.*` | Tier-B RSS feeds + LinkedIn search URLs |
| `sources.cold_outreach_person_sources.*` | Person-target source URLs |
| `rate_limits.*` | Per-API throttle hints (consumed by clients + scheduler) |
| `resume_path` / `cover_letter_path` | Asset locations for `load_profile` |

---

## OBSERVABILITY

- **Langfuse trace prefix:** `seek.*` (every node is wrapped with
  `@traced_node("...")` from `omerion_core/telemetry/middleware.py`).
- **Key metrics to watch:**
  - `postings_discovered` per platform per run
  - `relevance_filter_pass_rate` = `len(relevant_postings) / len(raw_postings)`
  - `avg_rank_score` over `ranked_postings`
  - `drafts_with_flags / len(drafts)`
  - `hitl_approval_rate` over the last N runs
  - `ghost_detection_rate` = `len(ghosts) / len(sent_in_window)`
  - `applications_per_week` — cumulative `submitted_count`
- **Skill-state surfacing:** the `agent-status` skill reads
  `agent_runs` rows and surfaces SEEK's last run timestamp + status in the
  weekly digest.
