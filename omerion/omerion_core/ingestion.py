"""
OMERION Backbone — Lead Ingestion & Dedup (A4)
================================================
Upserts contacts by linkedin_url (primary) or email (fallback).
Respects opt-out status. Never re-ingests an opted-out contact.

Integrates with: contacts, accounts (Supabase tables).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.optout import is_opted_out
from omerion_core.validation import validate_persona, validate_company_type

log = get_logger("omerion.backbone.ingestion")


@dataclass
class IngestionResult:
    action: str          # "created" | "updated" | "skipped"
    contact_id: str | None
    reason: str


def _normalize_url(url: str | None) -> str:
    """Normalize LinkedIn URL for dedup: lowercase, strip trailing slash."""
    if not url:
        return ""
    return url.strip().lower().rstrip("/")


def _normalize_email(email: str | None) -> str:
    if not email:
        return ""
    return email.strip().lower()


def _find_existing_contact(
    linkedin_url: str | None, email: str | None
) -> dict | None:
    """Find existing contact by linkedin_url (primary) or email (fallback)."""
    norm_li = _normalize_url(linkedin_url)
    norm_email = _normalize_email(email)

    if norm_li:
        try:
            result = supabase.table("contacts").select(
                "contact_id,do_not_contact,stage,persona,linkedin_url,email"
            ).ilike("linkedin_url", f"%{norm_li[-60:]}%").limit(1).execute()
            if result.data:
                return result.data[0]
        except Exception as exc:
            log.warning("ingestion_linkedin_lookup_error", error=str(exc))

    if norm_email:
        try:
            result = supabase.table("contacts").select(
                "contact_id,do_not_contact,stage,persona,linkedin_url,email"
            ).eq("email", norm_email).limit(1).execute()
            if result.data:
                return result.data[0]
        except Exception as exc:
            log.warning("ingestion_email_lookup_error", error=str(exc))

    return None


def ingest_lead(
    *,
    first_name: str = "",
    last_name: str = "",
    title: str = "",
    company: str = "",
    linkedin_url: str = "",
    email: str = "",
    phone: str = "",
    location: str = "",
    source: str = "linkedin_scrape",
) -> IngestionResult:
    """Ingest a single lead with upsert dedup logic.

    Returns IngestionResult with action taken and reason.
    """
    # Require at least one dedup key
    if not linkedin_url and not email:
        return IngestionResult(
            action="skipped", contact_id=None,
            reason="No linkedin_url or email — cannot deduplicate"
        )

    # Check for existing contact
    existing = _find_existing_contact(linkedin_url, email)

    if existing:
        contact_id = existing["contact_id"]

        # Opt-out guard: never re-ingest an opted-out contact
        if existing.get("do_not_contact") or existing.get("stage") == "do_not_contact":
            return IngestionResult(
                action="skipped", contact_id=contact_id,
                reason="Contact is opted out — will not re-ingest or overwrite"
            )

        # Update non-empty fields only (don't overwrite good data with blanks)
        updates = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if title and not existing.get("title"):
            updates["title"] = title
        if email and not existing.get("email"):
            updates["email"] = _normalize_email(email)
        if linkedin_url and not existing.get("linkedin_url"):
            updates["linkedin_url"] = _normalize_url(linkedin_url)

        if len(updates) > 1:  # more than just updated_at
            try:
                supabase.table("contacts").update(updates).eq(
                    "contact_id", contact_id
                ).execute()
            except Exception as exc:
                log.warning("ingestion_update_error", contact_id=contact_id, error=str(exc))

        return IngestionResult(
            action="updated", contact_id=contact_id,
            reason="Existing contact — non-empty fields updated"
        )

    # New contact — create
    contact_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    contact_row = {
        "contact_id": contact_id,
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "email": _normalize_email(email),
        "phone": phone.strip() if phone else None,
        "linkedin_url": _normalize_url(linkedin_url),
        "title": title.strip(),
        "persona": None,  # Scout will classify
        "fit_score": 0,
        "stage": "new",
        "source": source,
        "do_not_contact": False,
        "replied": False,
        "created_at": now,
        "updated_at": now,
    }

    try:
        supabase.table("contacts").insert(contact_row).execute()
        log.info("ingestion_contact_created", contact_id=contact_id,
                 name=f"{first_name} {last_name}".strip())
    except Exception as exc:
        log.error("ingestion_insert_error", contact_id=contact_id, error=str(exc))
        return IngestionResult(
            action="skipped", contact_id=None,
            reason=f"Insert failed: {exc}"
        )

    return IngestionResult(
        action="created", contact_id=contact_id,
        reason="New contact created — awaiting Scout enrichment"
    )


def ingest_batch(leads: list[dict]) -> list[IngestionResult]:
    """Process a batch of leads. Returns results in same order."""
    results = []
    for lead in leads:
        result = ingest_lead(**lead)
        results.append(result)
    created = sum(1 for r in results if r.action == "created")
    updated = sum(1 for r in results if r.action == "updated")
    skipped = sum(1 for r in results if r.action == "skipped")
    log.info("ingestion_batch_complete",
             total=len(leads), created=created, updated=updated, skipped=skipped)
    return results
