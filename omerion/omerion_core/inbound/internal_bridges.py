"""Webhook bridges — thin HTTP pass-through to existing Omerion clients.

Called by:
  • Hermes VPS (Claude Agent SDK on Hostinger)
  • Managed Agents on Anthropic Claude Console

All routes require Bearer auth (OMERION_WEBHOOK_TOKEN).  The Gmail send
path uses the OAuth refresh-token credentials already configured for
Railway (GOOGLE_OAUTH_CLIENT_ID / SECRET / REFRESH_TOKEN).
"""
from __future__ import annotations

import base64
import datetime
from email.message import EmailMessage
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from omerion_core.clients.google_client import gmail_service
from omerion_core.inbound.signatures import require_bearer
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.bridges")

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_bearer)],
)


# ── Email ───────────────────────────────────────────────────────────────────

class EmailRequest(BaseModel):
    to: str = Field(..., description="Recipient address")
    subject: str = Field(..., min_length=1, max_length=998)
    body: str = Field(..., min_length=1)
    cc: str | None = None
    reply_to: str | None = None
    html: bool = False


class EmailResponse(BaseModel):
    message_id: str
    thread_id: str


@router.post("/send-email", response_model=EmailResponse)
async def send_email(req: EmailRequest) -> EmailResponse:
    """Send an email from the configured Gmail account (evyevans.ai@gmail.com).

    Hermes and managed agents call this instead of SMTP or himalaya.
    userId='me' means the authenticated refresh-token owner is the sender.
    """
    msg = EmailMessage()
    msg["To"] = req.to
    msg["From"] = "me"
    msg["Subject"] = req.subject
    if req.cc:
        msg["Cc"] = req.cc
    if req.reply_to:
        msg["Reply-To"] = req.reply_to

    if req.html:
        msg.set_content("This message requires an HTML-capable email client.")
        msg.add_alternative(req.body, subtype="html")
    else:
        msg.set_content(req.body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        result = (
            gmail_service()
            .users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
    except Exception as exc:
        log.error("bridge_send_email_failed", to=req.to, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}") from exc

    log.info("bridge_send_email_ok", to=req.to, message_id=result.get("id"))
    return EmailResponse(
        message_id=result.get("id", ""),
        thread_id=result.get("threadId", ""),
    )


# ── Discord notify ───────────────────────────────────────────────────────────

class DiscordNotifyRequest(BaseModel):
    channel_id: str = Field(..., description="Discord channel snowflake ID")
    content: str = Field(..., min_length=1, max_length=2000)


class DiscordNotifyResponse(BaseModel):
    ok: bool
    detail: str = ""


@router.post("/discord/notify", response_model=DiscordNotifyResponse)
async def discord_notify(req: DiscordNotifyRequest) -> DiscordNotifyResponse:
    """Post a plain-text message to any Discord channel via the bot token.

    Managed agents use this for status updates and completion pings.
    """
    import httpx
    from omerion_core.settings import settings

    bot_token = getattr(settings, "discord_bot_token", None)
    if not bot_token:
        raise HTTPException(status_code=503, detail="DISCORD_BOT_TOKEN not configured")

    url = f"https://discord.com/api/v10/channels/{req.channel_id}/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
            json={"content": req.content},
        )

    if resp.status_code not in (200, 201):
        log.error("bridge_discord_notify_failed", channel=req.channel_id, status=resp.status_code)
        raise HTTPException(status_code=502, detail=f"Discord API {resp.status_code}: {resp.text}")

    log.info("bridge_discord_notify_ok", channel=req.channel_id)
    return DiscordNotifyResponse(ok=True)


# ── LinkedIn DM (stub — send via LinkedIn MCP on Hermes directly) ────────────

# ── R1 TRACK — rd_insights persistence ───────────────────────────────────────

_VALID_IMPACT_TAGS = frozenset({"daam", "capa", "remi", "asap", "internal_os"})
_VALID_PRIORITIES = frozenset({"high", "medium", "low"})


class RdInsightRow(BaseModel):
    source_url: str = Field(..., min_length=8)
    source_type: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    impact_tag: str
    estimated_priority: str
    raw_content: str | None = None
    metadata: dict[str, Any] | None = None


class RdInsightsBulkRequest(BaseModel):
    rows: list[RdInsightRow] = Field(..., min_length=1)
    run_date: str | None = None


class RdInsightsBulkResponse(BaseModel):
    supabase_upserts: int
    duplicates_dropped: int
    errors: list[str]


def _rd_insights_rest_upsert(rows: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    """Upsert rows to rd_insights via PostgREST (idempotent on source_url)."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return 0, 0, ["omerion_supabase_not_configured"]

    url = settings.supabase_url.rstrip("/")
    key = settings.supabase_service_role_key
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=representation",
    }
    endpoint = f"{url}/rest/v1/rd_insights?on_conflict=source_url"

    # Check existing URLs for duplicate accounting (ignore-duplicates returns all rows).
    source_urls = [r["source_url"] for r in rows]
    existing: set[str] = set()
    if source_urls:
        try:
            with httpx.Client(timeout=30.0) as client:
                for src in source_urls:
                    chk = client.get(
                        f"{url}/rest/v1/rd_insights",
                        headers={"apikey": key, "Authorization": f"Bearer {key}"},
                        params={"select": "source_url", "source_url": f"eq.{src}", "limit": 1},
                    )
                    if chk.status_code == 200 and chk.json():
                        existing.add(src)
        except Exception as exc:  # noqa: BLE001
            log.warning("rd_insights_prefetch_failed", error=str(exc))

    errors: list[str] = []
    upserts = 0
    duplicates = 0
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(endpoint, headers=headers, json=rows)
            if resp.status_code not in (200, 201):
                errors.append(f"postgrest_{resp.status_code}: {resp.text[:300]}")
                return 0, len(existing), errors
    except Exception as exc:  # noqa: BLE001
        log.error("rd_insights_bridge_post_failed", error=str(exc))
        return 0, len(existing), [str(exc)]

    for row in rows:
        if row["source_url"] in existing:
            duplicates += 1
        else:
            upserts += 1
    return upserts, duplicates, errors


@router.post("/rd/insights", response_model=RdInsightsBulkResponse)
async def upsert_rd_insights(req: RdInsightsBulkRequest) -> RdInsightsBulkResponse:
    """Persist tagged R1 insights to Supabase on behalf of cloud managed agents.

    Anthropic managed agents call this with OMERION_WEBHOOK_TOKEN so they do not
    need SUPABASE_* secrets in the Console credential vault.
    """
    payload: list[dict[str, Any]] = []
    for row in req.rows:
        if row.impact_tag not in _VALID_IMPACT_TAGS:
            raise HTTPException(
                status_code=400,
                detail=f"invalid impact_tag: {row.impact_tag}",
            )
        if row.estimated_priority not in _VALID_PRIORITIES:
            raise HTTPException(
                status_code=400,
                detail=f"invalid estimated_priority: {row.estimated_priority}",
            )
        meta = dict(row.metadata or {})
        if req.run_date:
            meta.setdefault("run_date", req.run_date)
        payload.append(
            {
                "source_url": row.source_url,
                "source_type": row.source_type,
                "title": row.title,
                "summary": row.summary,
                "impact_tag": row.impact_tag,
                "estimated_priority": row.estimated_priority,
                "raw_content": row.raw_content,
                "metadata": meta,
            }
        )

    upserts, duplicates, errors = _rd_insights_rest_upsert(payload)
    log.info(
        "rd_insights_bridge_ok",
        rows=len(payload),
        supabase_upserts=upserts,
        duplicates_dropped=duplicates,
        errors=len(errors),
    )
    return RdInsightsBulkResponse(
        supabase_upserts=upserts,
        duplicates_dropped=duplicates,
        errors=errors,
    )


# ── Shared Supabase REST helpers (server-side key) ───────────────────────────

def _sb_creds() -> tuple[str, str] | None:
    """Return (base_url, service_role_key) or None if not configured."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None
    return settings.supabase_url.rstrip("/"), settings.supabase_service_role_key


def _sb_get(table: str, params: dict[str, Any]) -> tuple[int, Any]:
    """GET against PostgREST with the server-held service-role key."""
    creds = _sb_creds()
    if not creds:
        return 0, "omerion_supabase_not_configured"
    url, key = creds
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{url}/rest/v1/{table}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                params=params,
            )
        return r.status_code, (r.json() if r.status_code == 200 else r.text[:300])
    except Exception as exc:  # noqa: BLE001
        log.error("sb_get_failed", table=table, error=str(exc))
        return 0, str(exc)


def _since(days: int) -> str:
    return (datetime.datetime.utcnow() - datetime.timedelta(days=days)).isoformat()


# ── R2 SEEK — rd_oss_candidates persistence (write) ──────────────────────────

_VALID_INTEGRATION_TYPES = frozenset(
    {"component", "pattern", "full_module", "reference_only"}
)


class OssCandidateRow(BaseModel):
    repo_url: str = Field(..., min_length=8)
    name: str = Field(..., min_length=1)
    description: str | None = None
    stars: int = 0
    language: str | None = None
    license: str | None = None
    search_tag: str | None = None
    integration_type: str = "reference_only"
    impact_tag: str = "asap"
    recommendation: str | None = None
    rubric_fit: float = 0.0
    rubric_maturity: float = 0.0
    rubric_composability: float = 0.0
    rubric_risk: float = 0.0
    overall_score: float | None = None  # computed if omitted
    scored_by: str = "haiku"


class OssCandidatesBulkRequest(BaseModel):
    rows: list[OssCandidateRow] = Field(..., min_length=1)


class OssCandidatesBulkResponse(BaseModel):
    upserts: int
    errors: list[str]


@router.post("/rd/oss-candidates", response_model=OssCandidatesBulkResponse)
async def upsert_oss_candidates(req: OssCandidatesBulkRequest) -> OssCandidatesBulkResponse:
    """Upsert R2 (SEEK) OSS candidates to Supabase, idempotent on repo_url.

    Managed agents call this with OMERION_WEBHOOK_TOKEN so they never need a
    SUPABASE_* secret in the Console credential vault. Mirrors /rd/insights.
    """
    creds = _sb_creds()
    if not creds:
        raise HTTPException(status_code=503, detail="omerion_supabase_not_configured")
    url, key = creds

    payload: list[dict[str, Any]] = []
    for row in req.rows:
        if row.impact_tag not in _VALID_IMPACT_TAGS:
            raise HTTPException(status_code=400, detail=f"invalid impact_tag: {row.impact_tag}")
        if row.integration_type not in _VALID_INTEGRATION_TYPES:
            raise HTTPException(
                status_code=400, detail=f"invalid integration_type: {row.integration_type}"
            )
        overall = row.overall_score
        if overall is None:
            overall = round(
                0.4 * row.rubric_fit
                + 0.3 * row.rubric_maturity
                + 0.2 * row.rubric_composability
                + 0.1 * (1.0 - row.rubric_risk),
                3,
            )
        payload.append(
            {
                "repo_url": row.repo_url,
                "name": row.name,
                "description": row.description,
                "stars": row.stars,
                "language": row.language,
                "license": row.license,
                "search_tag": row.search_tag,
                "integration_type": row.integration_type,
                "impact_tag": row.impact_tag,
                "recommendation": row.recommendation,
                "rubric_fit": row.rubric_fit,
                "rubric_maturity": row.rubric_maturity,
                "rubric_composability": row.rubric_composability,
                "rubric_risk": row.rubric_risk,
                "overall_score": overall,
                "scored_by": row.scored_by,
            }
        )

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    endpoint = f"{url}/rest/v1/rd_oss_candidates?on_conflict=repo_url"
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(endpoint, headers=headers, json=payload)
    except Exception as exc:  # noqa: BLE001
        log.error("oss_candidates_bridge_post_failed", error=str(exc))
        return OssCandidatesBulkResponse(upserts=0, errors=[str(exc)])

    if resp.status_code not in (200, 201, 204):
        err = f"postgrest_{resp.status_code}: {resp.text[:300]}"
        log.error("oss_candidates_bridge_bad_status", detail=err)
        return OssCandidatesBulkResponse(upserts=0, errors=[err])

    log.info("oss_candidates_bridge_ok", rows=len(payload))
    return OssCandidatesBulkResponse(upserts=len(payload), errors=[])


# ── R3 SHAPE — rd_proposals + founder_review_queue (write) ────────────────────

_VALID_PROPOSAL_STATUS = frozenset(
    {"draft", "submitted", "approved", "rejected", "in_build", "shipped", "retired"}
)


class ProposalRow(BaseModel):
    title: str = Field(..., min_length=1)
    problem: str = Field(..., min_length=1)
    proposed_change: str = Field(..., min_length=1)
    target_module: str | None = None
    affected_modules: list[str] = Field(default_factory=list)
    test_plan: str | None = None
    rollout_strategy: str | None = None
    impact_score: str
    effort_score: str
    priority_score: float | None = None
    source_insight_ids: list[str] = Field(default_factory=list)
    source_oss_ids: list[str] = Field(default_factory=list)
    status: str = "submitted"
    run_date: str | None = None
    # register is NOT a column — folded into the review card context for the founder.
    register: str | None = None


class ProposalsBulkRequest(BaseModel):
    rows: list[ProposalRow] = Field(..., min_length=1)
    session_id: str | None = None
    create_review_tasks: bool = True


class ProposalsBulkResponse(BaseModel):
    proposals_written: int
    review_rows_created: int
    proposal_ids: list[str]
    errors: list[str]


@router.post("/rd/proposals", response_model=ProposalsBulkResponse)
async def submit_rd_proposals(req: ProposalsBulkRequest) -> ProposalsBulkResponse:
    """Persist R3 (SHAPE) proposals and open a founder review per proposal.

    The agent supplies only proposal content + OMERION_WEBHOOK_TOKEN. The bridge
    writes rd_proposals (status='submitted' by default) and then mints the HITL
    approve/reject tokens via the canonical create_founder_review_task() — the
    agent must NOT mint its own tokens (they gate /hitl/resolve).
    """
    creds = _sb_creds()
    if not creds:
        raise HTTPException(status_code=503, detail="omerion_supabase_not_configured")
    url, key = creds

    payload: list[dict[str, Any]] = []
    registers: list[str | None] = []
    for row in req.rows:
        if row.status not in _VALID_PROPOSAL_STATUS:
            raise HTTPException(status_code=400, detail=f"invalid status: {row.status}")
        affected = row.affected_modules or ([row.target_module] if row.target_module else [])
        if not affected:
            raise HTTPException(
                status_code=400,
                detail=f"affected_modules required (NOT NULL): {row.title}",
            )
        item: dict[str, Any] = {
            "title": row.title,
            "problem": row.problem,
            "proposed_change": row.proposed_change,
            "target_module": row.target_module,
            "affected_modules": affected,
            "test_plan": row.test_plan,
            "rollout_strategy": row.rollout_strategy,
            "impact_score": row.impact_score,
            "effort_score": row.effort_score,
            "priority_score": row.priority_score,
            "source_insight_ids": row.source_insight_ids,
            "source_oss_ids": row.source_oss_ids,
            "status": row.status,
        }
        if row.run_date:
            item["run_date"] = row.run_date
        payload.append(item)
        registers.append(row.register)

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(f"{url}/rest/v1/rd_proposals", headers=headers, json=payload)
    except Exception as exc:  # noqa: BLE001
        log.error("rd_proposals_bridge_post_failed", error=str(exc))
        return ProposalsBulkResponse(
            proposals_written=0, review_rows_created=0, proposal_ids=[], errors=[str(exc)]
        )

    if resp.status_code not in (200, 201):
        err = f"postgrest_{resp.status_code}: {resp.text[:300]}"
        log.error("rd_proposals_bridge_bad_status", detail=err)
        return ProposalsBulkResponse(
            proposals_written=0, review_rows_created=0, proposal_ids=[], errors=[err]
        )

    created = resp.json()
    proposal_ids = [r["proposal_id"] for r in created]
    errors: list[str] = []
    review_count = 0

    if req.create_review_tasks:
        from omerion_core.hitl.review import create_founder_review_task

        session_id = req.session_id or f"managed:r3:{created[0].get('run_date', '')}"
        for r, reg in zip(created, registers):
            scope = f" · {reg}" if reg else ""
            try:
                create_founder_review_task(
                    agent_name="omerion.r3_strategic_architect",
                    session_id=session_id,
                    correlation_id=r["proposal_id"],
                    subject=f"R3 Proposal ({r.get('run_date', '')}) — {r['title']}",
                    context_md=(
                        f"**{r['title']}** · `{r.get('target_module')}`{scope} · "
                        f"impact `{r['impact_score']}` · effort `{r['effort_score']}` · "
                        f"RICE `{r.get('priority_score')}`\n\n"
                        f"**Problem:** {r['problem']}\n\n"
                        f"**Change:** {r['proposed_change'][:600]}"
                    ),
                    draft_ref={"table": "rd_proposals", "proposal_id": r["proposal_id"]},
                )
                review_count += 1
            except Exception as exc:  # noqa: BLE001
                log.error("rd_proposal_review_task_failed", proposal_id=r["proposal_id"], error=str(exc))
                errors.append(f"review_task_failed:{r['proposal_id']}:{exc}")

    log.info(
        "rd_proposals_bridge_ok",
        proposals_written=len(created),
        review_rows_created=review_count,
        errors=len(errors),
    )
    return ProposalsBulkResponse(
        proposals_written=len(created),
        review_rows_created=review_count,
        proposal_ids=proposal_ids,
        errors=errors,
    )


# ── R&D reads (so agents need no Supabase key for context loads) ──────────────

@router.get("/rd/insights")
async def read_rd_insights(impact_tag: str | None = None, since_days: int = 14) -> dict:
    """Read recent rd_insights (R2 seed / R3 context). Filtered on ingested_at."""
    params: dict[str, Any] = {"select": "*", "ingested_at": f"gte.{_since(since_days)}"}
    if impact_tag:
        params["impact_tag"] = f"eq.{impact_tag}"
    status, data = _sb_get("rd_insights", params)
    if status != 200:
        raise HTTPException(status_code=502, detail=f"supabase {status}: {data}")
    return {"rows": data, "count": len(data)}


@router.get("/rd/oss-candidates")
async def read_oss_candidates(
    min_fit: float = 0.5, max_risk: float = 0.7, since_days: int = 14
) -> dict:
    """Read recent rd_oss_candidates (R3 context). Filtered on created_at + rubric."""
    params = {
        "select": "*",
        "rubric_fit": f"gte.{min_fit}",
        "rubric_risk": f"lt.{max_risk}",
        "created_at": f"gte.{_since(since_days)}",
    }
    status, data = _sb_get("rd_oss_candidates", params)
    if status != 200:
        raise HTTPException(status_code=502, detail=f"supabase {status}: {data}")
    return {"rows": data, "count": len(data)}


@router.get("/rd/attribution-reports")
async def read_attribution_reports(since_days: int = 14) -> dict:
    """Read recent attribution_reports (R3 context). Filtered on computed_at."""
    params = {"select": "*", "computed_at": f"gte.{_since(since_days)}"}
    status, data = _sb_get("attribution_reports", params)
    if status != 200:
        raise HTTPException(status_code=502, detail=f"supabase {status}: {data}")
    return {"rows": data, "count": len(data)}


@router.post("/linkedin/send-dm")
async def linkedin_send_dm_stub() -> dict:
    """LinkedIn DM send is handled by the LinkedIn MCP directly on Hermes.

    Do not route LinkedIn writes through this bridge — call
    linkedin_queue_dm from the Hermes skill instead.
    """
    return {
        "queued": False,
        "detail": (
            "LinkedIn DM send goes through the LinkedIn MCP on Hermes. "
            "Call linkedin_queue_dm directly from the skill, not via this bridge."
        ),
    }
