from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.clients.google_client import gmail_service
from omerion_core.clients.supabase_client import supabase
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.logging import get_logger
from omerion_core.rate_limit.token_bucket import BUCKETS
from omerion_core.telemetry.middleware import traced_node

from .drive_sync import sync_drive_materials
from .state import NewsletterState, SubscriberGroup

log = get_logger("omerion.agents.newsletter_generator")

_RECENCY_DAYS = {"skillpack": 14, "playbook": 30, "newsletter": 7}


def _send_newsletter_email(to_addr: str, subject: str, text_body: str, html_body: str | None) -> str:
    """Send via SMTP with rate limiting. Returns mock message_id on success."""
    from omerion_core.clients.smtp_client import send_email_smtp
    BUCKETS["gmail"].acquire()
    
    logo_path = os.path.join(os.path.dirname(__file__), "templates", "logo.png")
    return send_email_smtp(to_addr, subject, text_body, html_body, logo_path)


@traced_node("sync_materials")
async def sync_materials_node(state: NewsletterState) -> NewsletterState:
    """Pull the latest uploaded files from Google Drive into newsletter_materials."""
    new_count = sync_drive_materials(state.mode)
    log.info("newsletter_drive_synced", mode=state.mode, new_materials=new_count)
    return state


@traced_node("fetch_subscribers")
async def fetch_subscribers_node(state: NewsletterState) -> NewsletterState:
    """Find subscribers who are due for the newsletter mode."""
    mode = state.mode
    now = datetime.now(timezone.utc)

    if mode == "skillpack":
        cutoff = now - timedelta(days=14)
        time_field = "last_skillpack_sent_at"
    elif mode == "playbook":
        cutoff = now - timedelta(days=30)
        time_field = "last_playbook_sent_at"
    else:
        cutoff = now - timedelta(days=7)
        time_field = "last_newsletter_sent_at"

    response = (
        supabase.table("newsletter_subscribers")
        .select("subscriber_id, email, industry")
        .eq("status", "active")
        .or_(f"{time_field}.is.null,{time_field}.lte.{cutoff.isoformat()}")
        .execute()
    )
    rows = response.data or []

    groups: dict[str, SubscriberGroup] = {}
    for r in rows:
        ind = r.get("industry", "General")
        if ind not in groups:
            groups[ind] = SubscriberGroup(industry=ind, subscriber_ids=[], emails=[])
        groups[ind].subscriber_ids.append(r["subscriber_id"])
        groups[ind].emails.append(r["email"])

    state.target_groups = list(groups.values())
    state.current_industry_index = 0
    state.emails_sent = 0
    state.errors = []

    log.info(
        "newsletter_subscribers_fetched",
        mode=mode,
        total_subscribers=len(rows),
        industries=len(state.target_groups),
    )
    return state


@traced_node("hitl_review")
def hitl_review_node(state: NewsletterState) -> NewsletterState:
    """G1 — build the founder review card before any email is sent.

    Shows the full send plan (groups × materials) so the founder can confirm
    the content and audience before a single email leaves the system.
    """
    if not state.target_groups:
        return state

    total_subscribers = sum(len(g.emails) for g in state.target_groups)
    run_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = (
        f"### Newsletter Send Plan — {state.mode} — {run_date_str}\n\n"
        f"**{len(state.target_groups)} industry groups · {total_subscribers} subscribers**\n\n"
    )
    for g in state.target_groups:
        body += f"- **{g.industry}**: {len(g.emails)} subscribers\n"
    body += (
        "\n\nApprove to send this batch. Reject to abort — no emails will be sent."
    )

    review = create_founder_review_task(
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        subject=(
            f"Newsletter batch — {state.mode} — {len(state.target_groups)} groups, "
            f"{total_subscribers} subscribers"
        ),
        context_md=body,
        draft_ref={
            "kind": "newsletter_batch",
            "mode": state.mode,
            "group_count": len(state.target_groups),
            "subscriber_count": total_subscribers,
        },
        correlation_id=state.correlation_id,
    )
    state.review_id = review["review_id"]
    log.info("newsletter_hitl_card_created", review_id=str(state.review_id))
    return state


@traced_node("hitl_wait")
def hitl_wait_node(state: NewsletterState) -> NewsletterState:
    """Suspend graph at interrupt(); PostgresSaver checkpoints here.

    Replay guard: if decision is already resolved (e.g. re-entered from checkpoint
    after resume), do NOT call interrupt() again.
    """
    if not state.review_id:
        return state
    if state.decision in ("approved", "rejected"):
        return state
    result = interrupt({"review_id": str(state.review_id), "session_id": state.session_id})
    decisions = result.get("decisions", {})
    state.decision = decisions.get(str(state.review_id), "rejected")
    log.info("newsletter_hitl_decision", decision=state.decision)
    return state


def route_after_hitl(state: NewsletterState) -> str:
    if state.decision != "approved":
        return "end"
    return "route_groups"


def route_groups(state: NewsletterState) -> str:
    if state.current_industry_index >= len(state.target_groups):
        return "end"
    return "generate_and_send"


@traced_node("generate_and_send")
async def generate_and_send_node(state: NewsletterState) -> NewsletterState:
    """Fetch content for the current industry group and send via Gmail API."""
    if state.decision != "approved":
        return state

    idx = state.current_industry_index
    group = state.target_groups[idx]
    mode = state.mode

    industry = group.industry
    sub_ids = group.subscriber_ids
    emails = group.emails

    log.info("newsletter_processing_industry", industry=industry, mode=mode, recipients=len(emails))

    if mode == "skillpack":
        mat_type = "skill_pack"
        template_file = "03-skills.html"
    elif mode == "playbook":
        mat_type = "playbook"
        template_file = "02-playbooks.html"
    else:
        mat_type = "newsletter"
        template_file = "01-weekly-newsletter.html"

    recency_cutoff = datetime.now(timezone.utc) - timedelta(days=_RECENCY_DAYS.get(mode, 14))

    materials_resp = (
        supabase.table("newsletter_materials")
        .select("id, title, drive_url, content_meta")
        .eq("industry", industry)
        .eq("material_type", mat_type)
        .gte("created_at", recency_cutoff.isoformat())
        .order("sequence_number")
        .execute()
    )
    materials = materials_resp.data or []
    if not materials:
        msg = f"No {mat_type} materials found for industry: {industry}"
        log.warning(msg)
        state.errors.append(msg)
        state.current_industry_index = idx + 1
        return state

    # Bulk-fetch delivery log for all subscribers in this group (avoids N+1).
    log_resp = (
        supabase.table("newsletter_delivery_log")
        .select("subscriber_email, material_id")
        .in_("subscriber_email", emails)
        .execute()
    )
    sent_by_email: dict[str, set[str]] = {}
    for r in (log_resp.data or []):
        sent_by_email.setdefault(r["subscriber_email"], set()).add(r["material_id"])

    # Load HTML template once per group.
    template_path = os.path.join(os.path.dirname(__file__), "templates", template_file)
    html_template = ""
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            html_template = f.read()
    else:
        log.warning("template_not_found", path=template_path)

    now_str = datetime.now(timezone.utc).isoformat()
    sent_count = 0
    sent_subscriber_ids: list[str] = []

    for email, sub_id in zip(emails, sub_ids):
        sent_material_ids = sent_by_email.get(email, set())
        pending_material = next((m for m in materials if m["id"] not in sent_material_ids), None)

        if not pending_material:
            log.info("subscriber_exhausted_materials", email=email, industry=industry)
            continue

        subject = f"Your {mat_type.replace('_', ' ').title()}: {pending_material['title']}"
        text_body = (
            f"Hello!\n\nHere is your {mat_type.replace('_', ' ')} tailored for the {industry} industry.\n\n"
            f"Access it here: {pending_material['drive_url']}\n\n"
            "Best,\nThe Omerion Team"
        )

        html_body: str | None = None
        if html_template:
            content_meta = dict(pending_material.get("content_meta") or {})
            # Map standard fields and allow replacing any key from content_meta
            content_meta["title"] = pending_material["title"]
            content_meta["drive_url"] = pending_material.get("drive_url") or ""
            content_meta["cta_url"] = pending_material.get("drive_url") or ""
            content_meta["industry_vertical"] = industry
            
            # Dynamically display skill cards based on presence of skill_x_name
            for i in range(1, 5):
                name_key = f"skill_{i}_name"
                disp_key = f"skill_{i}_display"
                if name_key in content_meta and content_meta[name_key]:
                    content_meta[disp_key] = "block"
                else:
                    content_meta[disp_key] = "none"

            rendered = html_template
            for k, v in content_meta.items():
                rendered = rendered.replace(f"{{{{{k}}}}}", str(v))
            html_body = re.sub(r"\{\{.*?\}\}", "", rendered)

        try:
            _send_newsletter_email(email, subject, text_body, html_body)
            supabase.table("newsletter_delivery_log").insert({
                "subscriber_email": email,
                "material_id": pending_material["id"],
            }).execute()
            sent_count += 1
            sent_subscriber_ids.append(sub_id)
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"Email failed to {email}: {exc}")

    # Update last_*_sent_at only for subscribers who actually received an email.
    if mode == "skillpack":
        time_field = "last_skillpack_sent_at"
    elif mode == "playbook":
        time_field = "last_playbook_sent_at"
    else:
        time_field = "last_newsletter_sent_at"

    if sent_subscriber_ids:
        try:
            supabase.table("newsletter_subscribers").update(
                {time_field: now_str}
            ).in_("subscriber_id", sent_subscriber_ids).execute()
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"Failed to update DB for {industry}: {exc}")

    state.current_industry_index = idx + 1
    state.emails_sent = state.emails_sent + sent_count
    return state


def build() -> StateGraph:
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(NewsletterState)

    g.add_node("sync_materials", sync_materials_node)
    g.add_node("fetch_subscribers", fetch_subscribers_node)
    g.add_node("hitl_review", hitl_review_node)
    g.add_node("hitl_wait", hitl_wait_node)
    g.add_node("generate_and_send", generate_and_send_node)

    g.set_entry_point("sync_materials")
    g.add_edge("sync_materials", "fetch_subscribers")
    g.add_edge("fetch_subscribers", "hitl_review")
    g.add_edge("hitl_review", "hitl_wait")
    g.add_conditional_edges("hitl_wait", route_after_hitl, {
        "end": END,
        "route_groups": "generate_and_send",
    })
    g.add_conditional_edges("generate_and_send", route_groups, {
        "end": END,
        "generate_and_send": "generate_and_send",
    })

    return g.compile(checkpointer=get_checkpointer())
