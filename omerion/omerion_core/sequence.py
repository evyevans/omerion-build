"""
OMERION Backbone — Sequence Counter (A6) + Template Selection (A7)
===================================================================
A6: Counts outreach touches per contact per channel.
    Includes BOTH sent comms AND pending review items.
    Enforces the 4-touch maximum per channel.

A7: Maps persona → template key for standard outreach,
    and company_type → template key for "Replace the Hire".
    Pure deterministic lookup — zero AI.
"""
from __future__ import annotations

from dataclasses import dataclass

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.backbone.sequence")

MAX_TOUCHES_PER_CHANNEL = 4


# ── A6: Sequence Counter ────────────────────────────────────────────────────

@dataclass
class SequencePosition:
    sent: int
    pending: int
    total: int
    is_complete: bool
    channel: str


def get_sequence_position(contact_id: str, channel: str) -> SequencePosition:
    """Count outreach touches for a contact on a specific channel.

    Includes BOTH sent comms (outbound_communications) AND
    pending review items (founder_review_queue).
    """
    sent_count = 0
    pending_count = 0

    # Count sent communications
    try:
        result = supabase.table("outbound_communications").select(
            "comm_id", count="exact"
        ).eq("contact_id", contact_id).eq(
            "channel", channel
        ).in_("status", ["sent", "replied"]).execute()
        sent_count = result.count or 0
    except Exception as exc:
        log.warning("sequence_sent_count_error",
                    contact_id=contact_id, channel=channel, error=str(exc))

    # Count pending review items
    try:
        result = supabase.table("founder_review_queue").select(
            "review_id", count="exact"
        ).eq("contact_id", contact_id).eq(
            "status", "pending"
        ).execute()
        pending_count = result.count or 0
    except Exception as exc:
        log.warning("sequence_pending_count_error",
                    contact_id=contact_id, error=str(exc))

    total = sent_count + pending_count
    return SequencePosition(
        sent=sent_count,
        pending=pending_count,
        total=total,
        is_complete=total >= MAX_TOUCHES_PER_CHANNEL,
        channel=channel,
    )


def is_sequence_complete(contact_id: str, channel: str) -> bool:
    """Quick check: has this contact hit 4+ touches on this channel?"""
    return get_sequence_position(contact_id, channel).is_complete


def is_fully_sequenced(contact_id: str) -> bool:
    """Check if contact has completed sequences on ALL channels."""
    email_done = is_sequence_complete(contact_id, "email")
    linkedin_done = is_sequence_complete(contact_id, "linkedin")
    return email_done and linkedin_done


# ── A7: Template Selection ───────────────────────────────────────────────────

# Persona → template key mapping (matches column order in Outreach Templates)
PERSONA_TEMPLATE_MAP: dict[str, str] = {
    "Operations Leader":            "ops_leader",
    "Revenue Leader":               "revenue_leader",
    "SME Founder":                  "sme_founder",
    "Agency Owner":                 "agency_owner",
    "E-commerce Operator":          "ecommerce_operator",
    "Professional Services Owner":  "professional_services_owner",
    "SaaS Founder":                 "saas_founder",
    "HR / Talent Leader":           "hr_talent_leader",
    "Finance Operations Leader":    "finance_ops",
}

# Company type → template key for "Replace the Hire" strategy
COMPANY_TYPE_TEMPLATE_MAP: dict[str, str] = {
    "Independent SMB (10-50 employees)":    "independent_smb",
    "Growth-Stage Startup (Series A/B)":    "growth_stage_startup",
    "Digital Marketing Agency":             "digital_marketing_agency",
    "Consulting Firm":                      "consulting_firm",
    "E-commerce Brand (DTC)":               "ecommerce_brand_dtc",
    "SaaS Company":                         "saas_company",
    "Law Firm":                             "law_firm",
    "Accounting / Finance Firm":            "accounting_finance_firm",
    "Staffing / HR Firm":                   "staffing_hr_firm",
    "Manufacturing Company":                "manufacturing_company",
    "Healthcare Practice":                  "healthcare_practice",
    "Professional Services Firm":           "professional_services_firm",
    "Media / Content Company":              "media_content_company",
    "Nonprofit Organization":               "nonprofit_organization",
}


@dataclass
class TemplateSelection:
    strategy: str          # "standard" | "replace_the_hire"
    template_key: str
    channel: str
    sequence_step: int
    error: str | None = None


def select_template(
    *,
    persona: str | None,
    channel: str,
    sequence_step: int,
    job_posting_url: str | None = None,
    company_type: str | None = None,
) -> TemplateSelection:
    """Select the correct outreach template.

    Strategy priority:
    1. If job_posting_url exists AND company_type is valid → "Replace the Hire"
    2. Otherwise → "Standard" persona-based template

    Returns TemplateSelection with the template_key to look up.
    """
    # Determine strategy
    if job_posting_url and job_posting_url.strip():
        # Replace the Hire path
        if not company_type or company_type == "NEEDS_REVIEW":
            return TemplateSelection(
                strategy="replace_the_hire",
                template_key="",
                channel=channel,
                sequence_step=sequence_step,
                error=f"company_type required for Replace the Hire — got: {company_type}"
            )
        template_key = COMPANY_TYPE_TEMPLATE_MAP.get(company_type)
        if not template_key:
            return TemplateSelection(
                strategy="replace_the_hire",
                template_key="",
                channel=channel,
                sequence_step=sequence_step,
                error=f"No template mapped for company_type: {company_type}"
            )
        return TemplateSelection(
            strategy="replace_the_hire",
            template_key=template_key,
            channel=channel,
            sequence_step=sequence_step,
        )

    # Standard persona path
    if not persona or persona == "NEEDS_REVIEW":
        return TemplateSelection(
            strategy="standard",
            template_key="",
            channel=channel,
            sequence_step=sequence_step,
            error=f"persona required for standard outreach — got: {persona}"
        )
    template_key = PERSONA_TEMPLATE_MAP.get(persona)
    if not template_key:
        return TemplateSelection(
            strategy="standard",
            template_key="",
            channel=channel,
            sequence_step=sequence_step,
            error=f"No template mapped for persona: {persona}"
        )
    return TemplateSelection(
        strategy="standard",
        template_key=template_key,
        channel=channel,
        sequence_step=sequence_step,
    )
