"""State for SEEK — Job Hunter sub-agent."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

# Platform values must stay in sync with the job_platform enum in
# infra/supabase/migrations/0017_seek_tables.sql + 0019_seek_extra_platforms.sql.
Platform = Literal[
    # Tier B — volume freelance + general boards
    "upwork", "linkedin_jobs", "indeed",
    # Google Jobs aggregates Indeed, LinkedIn, Glassdoor, ZipRecruiter + company pages
    "google_jobs",
    # Tier S — invite-only / curated freelance networks
    "toptal", "ateam", "braintrust", "contra",
    # Tier A — high-signal startup / automation-focused employer boards
    "wellfound", "yc", "lever", "greenhouse",
]
OpportunityKind = Literal["posting", "outreach_target"]


class JobPosting(BaseModel):
    posting_id: UUID = Field(default_factory=uuid4)
    platform: Platform
    external_id: str                            # URL hash or platform's own ID
    kind: OpportunityKind = "posting"           # "outreach_target" → cold-message a person
    title: str
    company: str = ""
    description: str = ""
    url: str
    target_name: str = ""                       # person's name when kind=="outreach_target"
    target_title: str = ""
    budget_low: float | None = None
    budget_high: float | None = None
    budget_type: Literal["hourly", "fixed", "salary", "unknown"] = "unknown"
    location: str = ""
    remote: bool = True
    posted_at: str | None = None                # ISO datetime string
    application_deadline: str | None = None     # ISO datetime; None when unstated
    required_skills: list[str] = Field(default_factory=list)
    company_domain: str | None = None           # e.g. "pacaso.com"; used by Hunter.io lookup
    relevance_score: float = 0.0                # set by filter_relevant node
    rank_score: float = 0.0                     # set by rank_opportunities node (0–10)
    rank_rationale: str = ""                    # one-line "why this rank" from the ranker
    rank: int = 0                               # ordinal position after ranking
    pinecone_id: str | None = None


class ApplicationDraft(BaseModel):
    draft_id: UUID = Field(default_factory=uuid4)
    posting_id: UUID
    platform: Platform
    kind: OpportunityKind
    cover_letter_body: str = ""                 # used for posting applications
    outreach_message: str = ""                  # used for outreach_target cold messages
    proposal_body: str = ""                     # Upwork-specific proposal text
    subject_line: str = ""                      # email subject for postings
    resume_attached: bool = True
    rank_score: float = 0.0                     # carried over from the posting
    hitl_flags: list[str] = Field(default_factory=list)
    hitl_notes: str = ""
    approved: bool = False
    application_db_id: UUID | None = None       # FK to job_applications once inserted


class SeekState(AgentRunState):
    agent_name: str = "biz_dev_outreach"
    run_date: date = Field(default_factory=date.today)

    # ─── Input overrides ────────────────────────────────────────────
    target_platforms: list[Platform] = Field(
        default_factory=lambda: [
            "toptal", "ateam", "braintrust", "contra",
            "wellfound", "yc", "lever", "greenhouse",
            "upwork", "linkedin_jobs", "indeed",
        ]
    )
    max_postings_per_platform: int = 15
    min_relevance_score: float = 0.65
    min_rank_score: float = 7.0

    # ─── Discovery phase ────────────────────────────────────────────
    raw_postings: list[JobPosting] = Field(default_factory=list)
    relevant_postings: list[JobPosting] = Field(default_factory=list)
    ranked_postings: list[JobPosting] = Field(default_factory=list)

    # ─── Application phase ──────────────────────────────────────────
    drafts: list[ApplicationDraft] = Field(default_factory=list)
    review_id: UUID | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"

    # ─── Counters ───────────────────────────────────────────────────
    submitted_count: int = 0
    skipped_duplicate: int = 0
    skipped_low_relevance: int = 0
    skipped_low_rank: int = 0
    skipped_scam: int = 0
    drafts_with_flags: int = 0

    # ─── Evykynn profile (loaded once per run) ──────────────────────
    resume_text: str = ""
    cover_letter_template: str = ""
