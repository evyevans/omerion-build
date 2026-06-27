# Omerion CRM — Google Sheets Schema

One master spreadsheet (id → `GOOGLE_CRM_SHEET_ID`) mirrors Supabase
tables that the founder needs to see or touch. Apps Script keeps the
tabs bidirectionally synced and wires up Approve/Reject buttons that
POST directly to the local Omerion FastAPI's `/hitl/resolve` route
(bearer auth). Direct hop from the sheet to the paused LangGraph thread.

All sheets include `id` as the first column (matches the Supabase PK)
and `synced_at` as the last column (ISO timestamp, Apps Script-written).

## Tabs

### `Contacts`
Mirror of `contacts`. Editable in Sheets for manual corrections; Apps
Script pushes diffs back to Supabase via the Omerion FastAPI mutate endpoint.

| col | type | source | notes |
|---|---|---|---|
| id | uuid | supabase | PK |
| account_id | uuid | supabase | FK |
| full_name | text | supabase | editable |
| email | text | supabase | editable, unique |
| linkedin_url | text | supabase | editable, unique |
| persona | enum | supabase | dropdown-validated |
| status | enum | supabase | dropdown |
| fit_score | int | supabase | read-only |
| intent_score | int | supabase | read-only |
| timing_score | int | supabase | read-only |
| last_touch_at | ts | supabase | read-only |
| synced_at | ts | apps_script | read-only |

### `Accounts`
Mirror of `accounts`. Read-mostly for founder review.

### `Opportunities`
Mirror of `opportunities`. Editable `stage` column with dropdown
(`discovery|qualified|proposal|closed_won|closed_lost`) — writes back
to Supabase on change.

### `Tasks`
Mirror of `build_tasks`. Read-only for founder visibility.

### `Review Queue`
Mirror of `founder_review_queue` **filtered to `decision='pending'`**.

| col | notes |
|---|---|
| review_id | uuid |
| agent_name | source agent |
| subject | short summary |
| context_md | rendered snippet |
| draft_link | opens Drive doc of draft |
| Approve | button → `onApprove(review_id)` |
| Reject | button → `onReject(review_id)` |
| approve_token | signed token — Apps Script POSTs this to `/hitl/resolve` on approve |
| reject_token | signed token — same for reject |
| created_at | |
| expires_at | 48h default |

### `Outreach Log`
Append-only mirror of `outbound_communications`. Filtered to last 30 days.

### `Deployments`
Mirror of `deployments`. Read-only.

### `Daily Digest`
Auto-regenerated tab. Agent #6 writes a single block per morning:
top-N scored contacts + yesterday's outreach summary + deployment events.
Apps Script `sendDigestEmail()` also emails this to the founder.

## Apps Script Triggers

- **onEdit(e)** — detects cell edits in `Contacts`, `Opportunities`,
  `Accounts`; pushes diff to Supabase via the Omerion FastAPI mutate endpoint.
- **pullFromSupabase()** — time-driven every 5 min, refreshes mirror tabs.
- **onApprove(reviewId) / onReject(reviewId)** — bound to button images
  in `Review Queue`; POSTs `{review_id, token, decision, decided_by}`
  with `Bearer ${OMERION_TOKEN}` to `${OMERION_BASE_URL}/hitl/resolve`.
  The handler validates the token (`secrets.compare_digest`), updates
  `founder_review_queue`, and resumes the paused LangGraph thread in
  the same request via the shared PostgresSaver.
- **sendDigestEmail()** — time-driven 06:30 daily; emails
  `evyevans.ai@gmail.com` the rendered `Daily Digest` tab.

## OAuth

- Personal Gmail access via OAuth refresh-token flow
  (`GOOGLE_OAUTH_CLIENT_ID`/`SECRET`/`REFRESH_TOKEN`). No service
  account / no domain-wide delegation — personal accounts don't
  support DWD.
- Writes from Sheets → Supabase go through the Omerion FastAPI (bearer
  token) rather than a direct Supabase connection, so every mutation is
  logged to `agent_actions`.
