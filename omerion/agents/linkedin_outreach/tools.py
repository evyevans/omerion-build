"""Tools for LinkedIn Outreach.

LinkedIn has no first-party send API; messages are queued in
`outbound_communications` with status='queued_for_sender' and a
companion sender (browser extension or third-party orchestrator) drains
the queue. This agent owns planning, drafting, HITL gating, and logging.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import NAMESPACE_OID, UUID, uuid5

from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.util.filtering import has_stop_condition
from omerion_core.util.time import parse_iso_utc

from .prompts import DRAFT_SYSTEM, DRAFT_USER
from .state import DraftedMessage, PlannedStep, Track

log = get_logger("omerion.agents.linkedin_outreach")


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def load_cohort(contact_ids: Iterable[UUID] | None = None, since_days: int = 14) -> list[dict]:
    """Pull contacts that have a LinkedIn URL and are eligible for outreach.

    If contact_ids supplied (e.g. from #6 shortlist), restrict to those.
    """
    q = supabase.table("contacts").select(
        "contact_id,account_id,first_name,last_name,linkedin_url,role,persona,"
        "last_touch_at,do_not_contact,replied,meeting_booked,explicit_no,"
        "accounts(name,domain,market,pain_signal)"
    ).not_.is_("linkedin_url", "null")
    if contact_ids:
        q = q.in_("contact_id", [str(c) for c in contact_ids])
    else:
        q = q.gte("updated_at", _ago(since_days))
    resp = q.execute()
    rows = resp.data or []
    cfg_stop = set(settings.agent("linkedin_outreach").get("stop_conditions", []))
    return [r for r in rows if not has_stop_condition(r, cfg_stop)]


def _last_li_touch(contact_id: UUID | str) -> datetime | None:
    resp = (
        supabase.table("outbound_communications")
        .select("sent_at,sequence_step")
        .eq("contact_id", str(contact_id))
        .eq("channel", "linkedin")
        .order("sent_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return None
    return parse_iso_utc(rows[0].get("sent_at"))


def _completed_steps(contact_id: UUID | str) -> int:
    resp = (
        supabase.table("outbound_communications")
        .select("sequence_step", count="exact")
        .eq("contact_id", str(contact_id))
        .eq("channel", "linkedin")
        .execute()
    )
    return resp.count or 0


def _classify_track(contact: dict) -> Track:
    cfg = settings.agent("linkedin_outreach").get("warm_contact_threshold", {})
    if cfg.get("email_reply") and contact.get("replied"):
        return "warm"
    if cfg.get("event_attended") and contact.get("event_attended"):
        return "warm"
    if cfg.get("mutual_engagement") and contact.get("mutual_engagement"):
        return "warm"
    return "cold"


def plan_steps(cohort: list[dict]) -> list[PlannedStep]:
    cfg = settings.agent("linkedin_outreach")
    cold = cfg.get("cold_sequence_steps", [])
    warm = cfg.get("warm_sequence_steps", [])
    out: list[PlannedStep] = []
    now = datetime.now(timezone.utc)

    for c in cohort:
        track = _classify_track(c)
        sequence = warm if track == "warm" else cold
        completed = _completed_steps(c["contact_id"])
        if completed >= len(sequence):
            continue
        step = sequence[completed]
        last_touch = _last_li_touch(c["contact_id"])
        days_since = (now - last_touch).days if last_touch else 999
        if days_since < step.get("day", 0):
            continue                                     # cooldown not met

        account = c.get("accounts") or {}
        out.append(PlannedStep(
            contact_id=UUID(c["contact_id"]),
            track=track,
            template_key=step["template"],
            step_type=step["type"],
            sequence_step=completed,
            cooldown_days=days_since,
            persona=c.get("persona") or "unknown",
            persona_tier=int(
                (settings.shared("personas").get(c.get("persona") or "") or {}).get("tier")
                or 3
            ),
            persona_variant=c.get("persona") or "unknown",
            first_name=c.get("first_name") or "",
            company=account.get("name", ""),
            pain_signal=account.get("pain_signal", "") or "",
            market=account.get("market", "") or "",
            outreach_hook=_pick_hook(c, account),
        ))
    return out


def _first_name(full_name: str) -> str:
    return full_name.split(" ")[0] if full_name else ""


def _pick_hook(contact: dict, account: dict) -> str:
    title = (contact.get("title") or "").strip()
    market = account.get("market") or ""
    if title and market:
        return f"{title} in {market}"
    return title or market or ""


def apply_daily_caps(steps: list[PlannedStep]) -> tuple[list[PlannedStep], int]:
    """Trim planned steps to today's connection + DM caps.

    Counts already-sent activity from `outbound_communications` for today.
    """
    cfg = settings.agent("linkedin_outreach")
    conn_cap = int(cfg.get("daily_connection_limit", 25))
    dm_cap = int(cfg.get("daily_message_limit", 40))

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = supabase.table("outbound_communications").select(
        "comm_id,template_key,sequence_step", count="exact"
    ).eq("channel", "linkedin").gte("sent_at", today_start.isoformat()).execute()
    rows = sent_today.data or []
    conn_used = sum(1 for r in rows if (r.get("template_key") or "").startswith("cold_connection"))
    dm_used = sum(1 for r in rows if not (r.get("template_key") or "").startswith("cold_connection"))

    kept: list[PlannedStep] = []
    skipped = 0
    for s in steps:
        if s.step_type == "connection_request":
            if conn_used >= conn_cap:
                skipped += 1
                continue
            conn_used += 1
        else:
            if dm_used >= dm_cap:
                skipped += 1
                continue
            dm_used += 1
        kept.append(s)
    return kept, skipped


def draft_message(router: ClaudeRouter, step: PlannedStep) -> DraftedMessage:
    resp = router.complete(
        system=DRAFT_SYSTEM,
        prompt=DRAFT_USER.format(
            track=step.track,
            step_type=step.step_type,
            template_key=step.template_key,
            persona=step.persona,
            persona_tier=step.persona_tier,
            persona_variant=step.persona_variant or step.persona,
            first_name=step.first_name,
            company=step.company,
            market=step.market,
            pain_signal=step.pain_signal,
            outreach_hook=step.outreach_hook,
        ),
        tier=Tier.DEFAULT,
        max_tokens=400,
        temperature=0.5,
    )
    body = (resp["text"] or "").strip()
    return DraftedMessage(
        step_id=step.step_id,
        contact_id=step.contact_id,
        template_key=step.template_key,
        track=step.track,
        step_type=step.step_type,
        body=body,
        char_count=len(body),
    )


def queue_for_sender(draft: DraftedMessage, sequence_id: UUID, sequence_step: int) -> str:
    """Insert into `outbound_communications` with status='queued_for_sender'.

    Idempotency: the key is a deterministic UUID5 over
    (contact + template_key + sha256(body)[:16] + date). A retry on the
    same day for the same contact+template with the **same body** hits
    the existing row via on_conflict. A retry with a **different body**
    produces a different key so we don't silently merge two distinct
    drafts into one outbound row.

    Wave 2.5: the body's content hash was added to the seed because the
    legacy form (contact + template + date) collided when the same
    contact/template was regenerated with edited copy — the second
    edit silently overwrote the first via upsert without delivering both
    or surfacing the conflict.
    """
    import hashlib

    body_hash = hashlib.sha256(draft.body.encode("utf-8")).hexdigest()[:16]
    date_str = datetime.now(timezone.utc).date().isoformat()
    # Seed parts are colon-separated and ordered (contact, template, hash, date)
    # so the seed parser in tests can verify the structure.
    seed = f"{draft.contact_id}:{draft.template_key}:{body_hash}:{date_str}"
    row = {
        "contact_id": str(draft.contact_id),
        "channel": "linkedin",
        "direction": "outbound",
        "sequence_id": str(sequence_id),
        "sequence_step": sequence_step,
        "template_key": draft.template_key,
        "body": draft.body,
        "status": "queued_for_sender",
        "idempotency_key": str(uuid5(NAMESPACE_OID, seed)),
    }
    # ignore_duplicates (NOT merge): if a row with this idempotency_key already
    # exists, do NOT overwrite it. A plain merge-upsert would re-stamp
    # status='queued_for_sender' onto a row that may already be 'sent' — which
    # send_queued_messages() would then re-drain, RE-SENDING a live LinkedIn DM
    # to the contact. With ignore_duplicates the existing row (and its status)
    # is preserved; on conflict no row is returned, so we fetch the existing
    # comm_id rather than IndexError on empty data.
    resp = (
        supabase.table("outbound_communications")
        .upsert(row, on_conflict="idempotency_key", ignore_duplicates=True)
        .execute()
    )
    if resp.data:
        return resp.data[0]["comm_id"]
    existing = (
        supabase.table("outbound_communications")
        .select("comm_id")
        .eq("idempotency_key", row["idempotency_key"])
        .limit(1)
        .execute()
    )
    return existing.data[0]["comm_id"] if existing.data else ""


def log_activity(contact_id: UUID, comm_id: str, activity_type: str, metadata: dict | None = None) -> None:
    # Guard against duplicate log rows on graph retry — same comm_id + activity_type is idempotent
    existing = (
        supabase.table("contact_activity_log")
        .select("activity_id")
        .eq("comm_id", comm_id)
        .eq("activity_type", activity_type)
        .limit(1)
        .execute()
    )
    if not existing.data:
        supabase.table("contact_activity_log").insert({
            "contact_id": str(contact_id),
            "activity_type": activity_type,
            "channel": "linkedin",
            "comm_id": comm_id,
            "metadata": metadata or {},
        }).execute()


# ─── LinkedIn browser-use sender ──────────────────────────────────────────────


def fetch_queued_messages(limit: int = 20) -> list[dict]:
    """Pull messages waiting to be sent via the LinkedIn browser sender."""
    resp = (
        supabase.table("outbound_communications")
        .select("comm_id,contact_id,body,template_key,sequence_step")
        .eq("channel", "linkedin")
        .eq("status", "queued_for_sender")
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    rows = resp.data or []
    if not rows:
        return []

    contact_ids = list({r["contact_id"] for r in rows})
    contacts_resp = (
        supabase.table("contacts")
        .select("contact_id,linkedin_url,first_name")
        .in_("contact_id", contact_ids)
        .execute()
    )
    contacts_by_id = {c["contact_id"]: c for c in (contacts_resp.data or [])}

    enriched: list[dict] = []
    for row in rows:
        contact = contacts_by_id.get(row["contact_id"]) or {}
        linkedin_url = contact.get("linkedin_url")
        if not linkedin_url:
            log.warning("reach_sender_no_linkedin_url", comm_id=row["comm_id"], contact_id=row["contact_id"])
            continue
        enriched.append({**row, "linkedin_url": linkedin_url, "first_name": contact.get("first_name", "")})
    return enriched


async def _send_single_linkedin_message(linkedin_url: str, body: str, timeout: float) -> str:
    """Navigate to a LinkedIn profile and send a message via native Playwright.

    Returns 'sent' on success, 'blocked' if LinkedIn requires login or CAPTCHA,
    or 'failed' on any other error.

    Uses deterministic CSS selectors rather than an LLM browser-use agent —
    the task (navigate → click Message → fill → click Send) maps directly to
    known LinkedIn DOM structure. This eliminates the langchain_anthropic
    dependency, ~500ms LLM latency, and per-DM Haiku cost.
    """
    try:
        from playwright.async_api import async_playwright
        from playwright.async_api import TimeoutError as PlaywrightTimeout
    except ImportError as exc:
        log.warning("reach_playwright_unavailable", error=str(exc))
        return "failed"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            ms = int(timeout * 800)
            await page.goto(linkedin_url, timeout=ms)

            # LinkedIn requires login if it redirects to /login or shows authwall.
            if "/login" in page.url or "authwall" in page.url:
                log.warning("reach_playwright_login_required", url=linkedin_url)
                return "blocked"

            # Click the Message button on the profile page.
            msg_btn = page.locator(
                'button[aria-label*="Message"], a[aria-label*="Message"]'
            ).first
            await msg_btn.click(timeout=5000)

            # Fill the message composer.
            editor = page.locator(
                '.msg-form__contenteditable, [data-placeholder="Write a message…"], '
                '[data-placeholder="Write a message..."]'
            ).first
            await editor.fill(body)

            # Submit.
            send_btn = page.locator(
                'button[type="submit"].msg-form__send-button, '
                'button[aria-label="Send"], '
                '.msg-form__send-button'
            ).first
            await send_btn.click(timeout=5000)

            return "sent"

        except PlaywrightTimeout:
            log.warning("reach_playwright_timeout", url=linkedin_url)
            return "blocked"
        except Exception as exc:  # noqa: BLE001
            log.warning("reach_playwright_error", url=linkedin_url, error=str(exc))
            return "failed"
        finally:
            await browser.close()


def send_queued_messages(limit: int = 10, timeout_per_message: float = 60.0) -> dict:
    """Drain the LinkedIn outbound queue using native Playwright.

    Processes up to `limit` queued messages. Returns a summary dict with
    counts of sent/blocked/failed outcomes.

    Requires Playwright to be installed:
        playwright install chromium
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    # Fail early with a clear error rather than discovering at Playwright launch time.
    cookie = settings.linkedin_session_cookie
    if not cookie:
        log.error(
            "linkedin_session_cookie_missing",
            hint="Set LINKEDIN_SESSION_COOKIE (the li_at cookie from LinkedIn DevTools → "
                 "Application → Cookies → linkedin.com → li_at) to enable sends.",
        )
        raise RuntimeError(
            "LINKEDIN_SESSION_COOKIE is required for linkedin_outreach send. "
            "Set it in .env or Railway environment variables."
        )

    queued = fetch_queued_messages(limit=limit)
    if not queued:
        log.info("reach_sender_queue_empty")
        return {"sent": 0, "blocked": 0, "failed": 0, "total": 0}

    results = {"sent": 0, "blocked": 0, "failed": 0, "total": len(queued)}

    def _run_in_thread(linkedin_url: str, body: str) -> str:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                _send_single_linkedin_message(linkedin_url, body, timeout_per_message)
            )
        finally:
            loop.close()

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reach-sender")

    for msg in queued:
        comm_id = msg["comm_id"]
        contact_id = msg["contact_id"]
        linkedin_url = msg["linkedin_url"]
        body = msg["body"]

        try:
            future = executor.submit(_run_in_thread, linkedin_url, body)
            outcome = future.result(timeout=timeout_per_message + 10)
        except Exception as exc:  # noqa: BLE001
            log.warning("reach_sender_thread_error", comm_id=comm_id, error=str(exc))
            outcome = "failed"

        new_status = "sent" if outcome == "sent" else ("blocked" if outcome == "blocked" else "failed")
        results[new_status] += 1

        try:
            from datetime import datetime, timezone
            supabase.table("outbound_communications").update({
                "status": new_status,
                "sent_at": datetime.now(timezone.utc).isoformat() if new_status == "sent" else None,
            }).eq("comm_id", comm_id).execute()

            if new_status == "sent":
                log_activity(UUID(contact_id), comm_id, "linkedin_sent",
                             {"template_key": msg.get("template_key"), "outcome": outcome})
        except Exception as exc:  # noqa: BLE001
            log.warning("reach_sender_db_update_failed", comm_id=comm_id, error=str(exc))

        log.info("reach_sender_outcome", comm_id=comm_id, outcome=outcome, linkedin_url=linkedin_url)

    executor.shutdown(wait=False)
    log.info("reach_sender_complete", **results)
    return results
