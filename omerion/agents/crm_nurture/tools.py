"""Tools for CRM Nurture (Agent #5).

Sends actual email messages on approval via Gmail.
Uses pg_advisory_lock to prevent two concurrent runs from double-touching
the same contact.
"""
from __future__ import annotations

import base64
import httpx
import json
import re
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Iterable
from uuid import NAMESPACE_OID, UUID, uuid5

from googleapiclient.errors import HttpError as GmailHttpError
from omerion_core.clients.google_client import gmail_service
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.rate_limit.token_bucket import BUCKETS
from omerion_core.retry import transient_retry
from omerion_core.settings import settings
from omerion_core.util.filtering import has_stop_condition
from omerion_core.util.scoring import engagement_score
from omerion_core.util.time import parse_iso_utc

from .prompts import EMAIL_SYSTEM, EMAIL_USER
from .state import NurtureCandidate, NurtureDraft

log = get_logger("omerion.agents.crm_nurture")


def parse_nurture_intent(router, message: str) -> tuple[dict, dict]:
    from .prompts import INTENT_SYSTEM
    from omerion_core.llm.router import Tier

    resp = router.complete(
        tier=Tier.FAST,
        system=INTENT_SYSTEM,
        prompt=message,
        max_tokens=150,
        temperature=0.0,
    )
    text = resp.get("text", "{}").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        return json.loads(text), resp
    except Exception:
        return {"contact_name": "", "contact_email": "", "custom_instructions": ""}, resp


def find_contact_id_by_name(name: str, email: str = "") -> str | None:
    """Resolve a contact_id from a name (and optional email). Returns None on miss.

    NEVER fabricates a contact. This agent sends LIVE email — inventing a recipient
    (the old behaviour: auto-inserting a `@example.com` row) risks drafting and
    sending to a non-existent person and defeats the caller's "contact not found"
    guard. A miss must surface to the founder, not be papered over.
    """
    # Prefer an exact email match when the founder supplied one — it's precise.
    if email:
        resp = supabase.table("contacts").select("contact_id").eq("email", email).limit(1).execute()
        if resp.data:
            return resp.data[0]["contact_id"]
    if not name:
        return None
    first_name = name.split()[0]
    resp = (
        supabase.table("contacts")
        .select("contact_id")
        .ilike("first_name", f"%{first_name}%")
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]["contact_id"]
    return None  # caller raises UserFacingError — do not invent a contact


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def load_candidates(contact_ids: Iterable[UUID] | None = None, since_days: int = 14) -> list[NurtureCandidate]:
    """Pull contacts in nurture-eligible stages, with stop-conditions filtered out."""
    cfg_stop = set(settings.agent("crm_nurture").get("stop_conditions", []))
    q = supabase.table("contacts").select(
        "contact_id,account_id,first_name,last_name,email,phone,persona,stage,"
        "last_touch_at,last_touch_reference,do_not_contact,replied,"
        "explicit_no,signed_agreement,meeting_booked,"
        "accounts(name,market,pain_signal)"
    ).in_("stage", ["new_lead", "contacted", "engaged", "proposal_sent"])
    if contact_ids:
        q = q.in_("contact_id", [str(c) for c in contact_ids])
    else:
        q = q.gte("updated_at", _ago(since_days)).order("last_touch_at", desc=True).limit(200)
    rows = (q.execute().data or [])
    if len(rows) == 200:
        log.warning("nurture_load_candidates_limit_hit", hint="200-row cap reached; some contacts may be skipped this cycle")

    out: list[NurtureCandidate] = []
    for r in rows:
        if has_stop_condition(r, cfg_stop):
            continue
        last = r.get("last_touch_at")
        last_dt = parse_iso_utc(last)
        days_since = max(0, (datetime.now(timezone.utc) - last_dt).days) if last_dt else 999
        account = r.get("accounts") or {}
        first_name = r.get("first_name") or _first_name(
            " ".join(filter(None, [r.get("first_name"), r.get("last_name")]))
        )
        out.append(NurtureCandidate(
            contact_id=UUID(r["contact_id"]),
            account_id=UUID(r["account_id"]) if r.get("account_id") else None,
            persona=r.get("persona") or "unknown",
            stage=r.get("stage") or "new_lead",
            first_name=first_name,
            email=r.get("email"),
            phone=r.get("phone"),
            pain_signal=account.get("pain_signal") or "",
            market=account.get("market") or "",
            last_touch_at=last,
            last_touch_reference=r.get("last_touch_reference") or "",
            days_since_last_touch=days_since,
            engagement_score=_engagement_score(r["contact_id"]),
        ))
    return out


def _first_name(full_name: str) -> str:
    return full_name.split(" ")[0] if full_name else ""


def _engagement_score(contact_id: str) -> float:
    """Opens(1.0) + clicks(3.0) over the last 24h. Uncapped on purpose: this score
    feeds an escalation threshold, so we want raw signal density, not a 0–1 bucket.
    """
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    resp = (
        supabase.table("contact_activity_log")
        .select("activity_type")
        .eq("contact_id", contact_id)
        .gte("occurred_at", since)
        .execute()
    )
    return engagement_score(resp.data or [])


def needs_touch(candidate: NurtureCandidate) -> bool:
    """Stage cooldown gating + escalation-on-engagement override."""
    cfg = settings.agent("crm_nurture")
    cooldown = cfg.get("cooldown_periods", {}).get(candidate.stage, 99)
    if candidate.days_since_last_touch < cooldown:
        # Engagement-driven escalation can bypass cooldown.
        thr = cfg.get("escalation_threshold", {})
        opens = thr.get("opens_in_24h", 99)
        clicks = thr.get("link_clicks", 99)
        if candidate.engagement_score < (opens * 1.0 + clicks * 3.0) / 2.0:
            return False
    return True


_SUBJECT_RE = re.compile(r"^SUBJECT:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_EXTERNAL_TAG_RE = re.compile(r"^\[External\]\s*", re.IGNORECASE)
_BODY_RE = re.compile(r"^BODY:\s*(.*)\Z", re.MULTILINE | re.IGNORECASE | re.DOTALL)


def _split_email(raw: str) -> tuple[str, str]:
    s = _SUBJECT_RE.search(raw or "")
    b = _BODY_RE.search(raw or "")
    subject = _EXTERNAL_TAG_RE.sub("", s.group(1).strip() if s else "Quick note").strip()
    body = (b.group(1).strip() if b else (raw or "").strip())
    return subject, body


def draft_email(router: ClaudeRouter, candidate: NurtureCandidate, template_key: str) -> NurtureDraft:
    resp = router.complete(
        system=EMAIL_SYSTEM.format(
            stage=candidate.stage,
            persona=candidate.persona,
            custom_instructions=candidate.custom_instructions,
        ),
        prompt=EMAIL_USER.format(
            stage=candidate.stage, persona=candidate.persona,
            first_name=candidate.first_name, market=candidate.market,
            pain_signal=candidate.pain_signal,
            last_touch_reference=candidate.last_touch_reference,
            days_since_last_touch=candidate.days_since_last_touch,
            template_key=template_key,
            custom_instructions=candidate.custom_instructions,
            # Recalled "what worked before" for this persona/stage (Pinecone
            # outreach_signals). Computed by rag_augment — now actually used.
            rag_context=candidate.rag_context or "(no prior winning angle on file)",
        ),
        tier=Tier.DEFAULT,
        max_tokens=600,
        temperature=0.4,
    )
    subject, body = _split_email(resp["text"])
    
    draft_id = ""
    try:
        draft_id = create_gmail_draft(candidate.email or "", subject, body)
    except Exception as exc:
        log.warning("failed_to_create_gmail_draft", error=str(exc))
        
    return NurtureDraft(
        contact_id=candidate.contact_id, channel="email", template_key=template_key,
        subject=subject, body=body, persona=candidate.persona, gmail_draft_id=draft_id
    )


def draft_for(router: ClaudeRouter, candidate: NurtureCandidate) -> NurtureDraft | None:
    """Draft an email for a candidate. Email is the only live channel today."""
    if not candidate.email:
        return None
    return draft_email(router, candidate, f"email_{candidate.stage}_v1")


@transient_retry(attempts=3, min_wait=2, max_wait=30, exceptions=(GmailHttpError, httpx.TimeoutException))
def create_gmail_draft(to_addr: str, subject: str, body_md: str) -> str:
    msg = MIMEText(body_md, "plain", "utf-8")
    msg["to"] = to_addr
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    message = {"message": {"raw": raw}}
    draft = gmail_service().users().drafts().create(userId="me", body=message).execute()
    return draft.get("id") or ""


@transient_retry(attempts=3, min_wait=2, max_wait=30, exceptions=(GmailHttpError, httpx.TimeoutException))
def send_gmail_draft(draft_id: str) -> str:
    sent = gmail_service().users().drafts().send(userId="me", body={"id": draft_id}).execute()
    return sent.get("id") or ""


@transient_retry(attempts=3, min_wait=2, max_wait=30, exceptions=(GmailHttpError, httpx.TimeoutException))
def send_email(to_addr: str, subject: str, body_md: str) -> str:
    msg = MIMEText(body_md, "plain", "utf-8")
    msg["to"] = to_addr
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = gmail_service().users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent.get("id") or ""


def deliver(draft: NurtureDraft, candidate: NurtureCandidate) -> str:
    if not candidate.email:
        raise ValueError(f"contact {candidate.contact_id} has no email; cannot deliver email draft")
    BUCKETS["gmail"].acquire()
    used_fallback = False
    if draft.gmail_draft_id:
        try:
            return send_gmail_draft(draft.gmail_draft_id)
        except GmailHttpError as exc:
            if exc.resp.status == 403:
                log.error("gmail_auth_failure", hint="Rotate GOOGLE_OAUTH_REFRESH_TOKEN")
                raise
            log.warning("gmail_draft_send_failed", status=exc.resp.status, fallback=True)
            used_fallback = True
        except Exception as exc:
            log.warning("gmail_draft_send_failed", error=str(exc), fallback=True)
            used_fallback = True
    try:
        msg_id = send_email(candidate.email, draft.subject, draft.body)
        if used_fallback:
            log.info("gmail_fallback_sent", contact_id=str(draft.contact_id), draft_id=draft.gmail_draft_id)
        return msg_id
    except Exception as exc:
        log.error("gmail_send_failed_both_paths", contact_id=str(draft.contact_id), error=str(exc))
        raise


def log_outbound(draft: NurtureDraft, candidate: NurtureCandidate, provider_id: str, channel_detail: str = "draft") -> str:
    seed = f"{draft.contact_id}:{draft.template_key}:{datetime.now(timezone.utc).date().isoformat()}"
    row = {
        "contact_id": str(draft.contact_id),
        "channel": draft.channel,
        "direction": "outbound",
        "template_key": draft.template_key,
        "subject": draft.subject if draft.channel == "email" else None,
        "body": draft.body,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "provider_id": provider_id,
        "status": "sent",
        "idempotency_key": str(uuid5(NAMESPACE_OID, seed)),
    }
    resp = (
        supabase.table("outbound_communications")
        .upsert(row, on_conflict="idempotency_key", ignore_duplicates=True)
        .execute()
    )
    if resp.data:
        comm_id = resp.data[0]["comm_id"]
    else:
        # ignore_duplicates=True returns [] on conflict; fetch the existing row.
        existing = (
            supabase.table("outbound_communications")
            .select("comm_id")
            .eq("idempotency_key", row["idempotency_key"])
            .limit(1)
            .execute()
        )
        comm_id = existing.data[0]["comm_id"] if existing.data else ""
    activity_type = f"{draft.channel}_sent"
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
            "contact_id": str(draft.contact_id),
            "activity_type": activity_type,
            "channel": draft.channel,
            "comm_id": comm_id,
            "metadata": {"template_key": draft.template_key, "persona": candidate.persona,
                         "channel_detail": channel_detail, "draft_id": draft.gmail_draft_id},
        }).execute()
    return comm_id


def acquire_advisory_lock(contact_id: UUID) -> bool:
    """pg_try_advisory_xact_lock keyed on contact_id hash."""
    try:
        resp = supabase.rpc("try_lock_contact", {"p_contact_id": str(contact_id)}).execute()
        return bool(resp.data)
    except Exception as exc:  # noqa: BLE001
        # Fail-closed: RPC failure means Supabase is degraded — exactly when duplicate risk
        # is highest. Skip the contact; the founder sees a lower sent_count, not a double-send.
        log.error("advisory_lock_rpc_failed", contact_id=str(contact_id), error=str(exc))
        return False
