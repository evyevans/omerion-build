"""Unit tests for SEEK — Job Hunter sub-agent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.biz_dev_outreach.state import ApplicationDraft, JobPosting, SeekState
from agents.biz_dev_outreach.tools import (
    _epoch_ms_to_iso,
    _extract_recipient_email,
    _extract_section,
    _jaccard_overlap,
    _looks_like_person,
    _parse_upwork_budget,
    _strip_html,
    dedup_postings,
    fetch_google_jobs,
    fetch_greenhouse_board,
    fetch_lever_board,
    flag_application_risks,
)


# ─── Unit: budget parsing ─────────────────────────────────────────────────────


def test_parse_upwork_budget_hourly():
    summary = "Looking for help. Budget: $50.00 - $75.00 /hr"
    lo, hi, btype = _parse_upwork_budget(summary)
    assert btype == "hourly"
    assert lo == 50.0
    assert hi == 75.0


def test_parse_upwork_budget_fixed():
    summary = "Fixed project. Budget: $1,500"
    lo, hi, btype = _parse_upwork_budget(summary)
    assert btype == "fixed"
    assert lo == 1500.0


def test_parse_upwork_budget_unknown():
    lo, hi, btype = _parse_upwork_budget("No budget info here.")
    assert btype == "unknown"
    assert lo is None


# ─── Unit: person detection heuristic ────────────────────────────────────────


def test_looks_like_person_yes():
    assert _looks_like_person("Sarah Chen", "Head of Operations") is True


def test_looks_like_person_no_job_title():
    assert _looks_like_person("Senior Developer", "AI Engineer") is False


# ─── Unit: section extraction ─────────────────────────────────────────────────


def test_extract_section_present():
    text = "COVER_LETTER:\nHi there.\n\nPROPOSAL:\nGreat fit.\n\nSUBJECT:"
    assert _extract_section(text, "COVER_LETTER") == "Hi there."
    assert _extract_section(text, "PROPOSAL") == "Great fit."


def test_extract_section_missing():
    assert _extract_section("", "COVER_LETTER") == ""


# ─── Unit: dedup postings ─────────────────────────────────────────────────────


def test_dedup_postings_filters_existing():
    existing_ext_id = "abc123"
    postings = [
        JobPosting(platform="upwork", external_id=existing_ext_id, title="Existing", url="http://a"),
        JobPosting(platform="upwork", external_id="new_id_456", title="New", url="http://b"),
    ]
    mock_result = MagicMock()
    mock_result.data = [{"platform": "upwork", "external_id": existing_ext_id}]

    mock_supa = MagicMock()
    mock_supa.table.return_value.select.return_value.execute.return_value = mock_result

    # Use new= to avoid Python 3.14 patch inspecting _SupabaseProxy.__getattr__
    with patch("agents.biz_dev_outreach.tools.supabase", new=mock_supa):
        result = dedup_postings(postings)

    assert len(result) == 1
    assert result[0].external_id == "new_id_456"


def test_dedup_postings_empty():
    assert dedup_postings([]) == []


# ─── Unit: draft_application output parsing ────────────────────────────────────


def test_draft_application_parses_cover_letter():
    from agents.biz_dev_outreach.tools import draft_application

    posting = JobPosting(
        platform="indeed",
        external_id="test_001",
        title="AI Consultant",
        company="Acme Growth Co",
        description="Need AI help.",
        url="http://example.com",
    )
    mock_router = MagicMock()
    mock_router.complete.return_value = {
        "text": (
            "COVER_LETTER:\nDear Acme Growth Co, I can help.\n\n"
            "PROPOSAL:\n\n"
            "OUTREACH_MESSAGE:\n\n"
            "SUBJECT:\nAI Consulting for Acme Growth Co"
        ),
    }
    draft = draft_application(mock_router, posting, "Resume text", "Template text")
    assert "I can help" in draft.cover_letter_body
    assert draft.subject_line == "AI Consulting for Acme Growth Co"
    assert draft.proposal_body == ""


def test_draft_application_upwork_proposal():
    from agents.biz_dev_outreach.tools import draft_application

    posting = JobPosting(
        platform="upwork",
        external_id="test_002",
        title="LangGraph Agent Build",
        url="http://upwork.com/job/1",
    )
    mock_router = MagicMock()
    mock_router.complete.return_value = {
        "text": (
            "COVER_LETTER:\nLetter body.\n\n"
            "PROPOSAL:\nShort Upwork pitch.\n\n"
            "OUTREACH_MESSAGE:\n\n"
            "SUBJECT:"
        ),
    }
    draft = draft_application(mock_router, posting, "Resume", "Template")
    assert draft.proposal_body == "Short Upwork pitch."


# ─── Unit: hardened budget regex ─────────────────────────────────────────────


@pytest.mark.parametrize("summary,expected", [
    ("Need help. $50 to $75/hr", (50.0, 75.0, "hourly")),
    ("Rate: $80–120 per hour", (80.0, 120.0, "hourly")),
    ("Less than $500 budget", (0.0, 500.0, "fixed")),
    ("Est. budget: $5,000", (5000.0, 5000.0, "fixed")),
    ("Fixed-price $2,500", (2500.0, 2500.0, "fixed")),
])
def test_parse_upwork_budget_variants(summary, expected):
    lo, hi, btype = _parse_upwork_budget(summary)
    assert (lo, hi, btype) == expected


# ─── Unit: html / time helpers ───────────────────────────────────────────────


def test_strip_html_basic():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_empty():
    assert _strip_html("") == ""


def test_epoch_ms_to_iso_valid():
    iso = _epoch_ms_to_iso(1714521600000)  # 2024-05-01 UTC
    assert iso is not None and iso.startswith("2024-05-01")


def test_epoch_ms_to_iso_none():
    assert _epoch_ms_to_iso(None) is None
    assert _epoch_ms_to_iso(0) is None


# ─── Unit: jaccard overlap ───────────────────────────────────────────────────


def test_jaccard_identical():
    assert _jaccard_overlap("hello world", "hello world") == 1.0


def test_jaccard_disjoint():
    assert _jaccard_overlap("alpha beta", "gamma delta") == 0.0


def test_jaccard_partial():
    score = _jaccard_overlap("ai consulting operations automation",
                             "ai consulting ops workflow")
    assert 0.3 < score < 0.6  # 2/5 = 0.4 expected


def test_jaccard_empty():
    assert _jaccard_overlap("", "anything") == 0.0


# ─── Unit: recipient email extraction ────────────────────────────────────────


def test_extract_recipient_email_present():
    posting = JobPosting(
        platform="indeed",
        external_id="x",
        title="t",
        url="http://x",
        description="Apply by emailing hiring@acmegrowth.com directly.",
    )
    assert _extract_recipient_email(posting) == "hiring@acmegrowth.com"


def test_extract_recipient_email_filters_noreply():
    posting = JobPosting(
        platform="indeed",
        external_id="x",
        title="t",
        url="http://x",
        description="Reply to noreply@example.com to apply.",
    )
    assert _extract_recipient_email(posting) is None


def test_extract_recipient_email_absent():
    posting = JobPosting(platform="indeed", external_id="x", title="t",
                         url="http://x", description="No email here.")
    assert _extract_recipient_email(posting) is None


# ─── Unit: flag_application_risks ────────────────────────────────────────────


def _make_draft(rank=8.0, body="A unique cover letter mentioning AI automation consulting.",
                kind="posting", platform="lever"):
    return ApplicationDraft(
        posting_id=uuid4(),
        platform=platform,
        kind=kind,
        cover_letter_body=body,
        rank_score=rank,
    )


def _make_posting(**overrides):
    base = dict(
        platform="lever",
        external_id="ext_" + uuid4().hex[:8],
        title="AI Engineer",
        company="Pacaso",
        description="Build AI workflows for our operations automation team. "
                    "Need someone with LangGraph and Claude API experience. "
                    "Remote, contract." * 3,
        url="http://example.com/job",
        budget_low=80.0,
        budget_high=120.0,
        budget_type="hourly",
    )
    base.update(overrides)
    return JobPosting(**base)


_THRESHOLDS = {
    "low_rank_score": 7.5,
    "short_deadline_days": 7,
    "duplicate_company_days": 30,
    "identical_cover_overlap": 0.70,
}


def test_flag_risks_clean_draft_has_no_flags():
    draft = _make_draft(rank=9.0)
    posting = _make_posting()
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, notes = flag_application_risks(draft, posting, [draft], [], _THRESHOLDS)
    assert flags == []
    assert notes == "clean"


def test_flag_risks_low_rank_score():
    draft = _make_draft(rank=6.0)
    posting = _make_posting()
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(draft, posting, [draft], [], _THRESHOLDS)
    assert "low_rank_score" in flags


def test_flag_risks_emits_scam_flag():
    draft = _make_draft()
    posting = _make_posting(
        description="Make money fast! Easy money guaranteed income, no experience needed."
    )
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(draft, posting, [draft], [], _THRESHOLDS)
    assert "scam_signal" in flags


def test_flag_risks_missing_budget():
    draft = _make_draft()
    posting = _make_posting(budget_low=None, budget_high=None, budget_type="unknown")
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(draft, posting, [draft], [], _THRESHOLDS)
    assert "missing_budget" in flags


def test_flag_risks_forbidden_keyword():
    draft = _make_draft()
    posting = _make_posting(company="MLM Crypto Pyramid LLC")
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(
            draft, posting, [draft], ["MLM", "crypto giveaway"], _THRESHOLDS)
    assert "forbidden_keyword" in flags


def test_flag_risks_vague_scope():
    draft = _make_draft()
    posting = _make_posting(description="Need help with AI.")
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(draft, posting, [draft], [], _THRESHOLDS)
    assert "vague_scope" in flags


def test_flag_risks_off_brand_voice_codename():
    draft = _make_draft(body="I am thrilled to apply! I built DAAM for their ops team.")
    posting = _make_posting()
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(draft, posting, [draft], [], _THRESHOLDS)
    assert "off_brand_voice" in flags


def test_flag_risks_detects_duplicate_cover():
    body = ("This cover letter mentions LangGraph Claude Pinecone Supabase ops "
            "AI automation consulting agentic systems multi-agent.")
    a = _make_draft(body=body)
    b = _make_draft(body=body + " Slight tweak.")
    posting = _make_posting()
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(a, posting, [a, b], [], _THRESHOLDS)
    assert "identical_cover_text" in flags


def test_flag_risks_short_deadline():
    draft = _make_draft()
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    posting = _make_posting(application_deadline=soon)
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(draft, posting, [draft], [], _THRESHOLDS)
    assert "short_deadline" in flags


def test_flag_risks_skill_mismatch():
    draft = _make_draft(body="I have experience with general AI work.")
    posting = _make_posting(
        required_skills=["Rust", "Solidity", "WebAssembly", "Kubernetes"],
        description="Senior role focused on blockchain infrastructure." * 8,
    )
    with patch("agents.biz_dev_outreach.tools._company_recently_applied", return_value=False):
        flags, _ = flag_application_risks(draft, posting, [draft], [], _THRESHOLDS)
    assert "skill_mismatch" in flags


# ─── Unit: Lever / Greenhouse JSON board fetchers ────────────────────────────


def test_lever_fetcher_parses_jobs():
    fake_payload = [
        {
            "id": "abc-123",
            "text": "Senior AI Engineer",
            "hostedUrl": "https://jobs.lever.co/pacaso/abc-123",
            "descriptionPlain": "Build agent workflows for our ops automation team.",
            "createdAt": 1714521600000,
            "categories": {"location": "Remote, US"},
        }
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_payload
    mock_resp.raise_for_status = MagicMock()

    with patch("agents.biz_dev_outreach.tools.httpx.get", return_value=mock_resp), \
         patch("agents.biz_dev_outreach.tools.time.sleep"):
        postings = fetch_lever_board(["pacaso"])

    assert len(postings) == 1
    p = postings[0]
    assert p.platform == "lever"
    assert p.external_id == "abc-123"
    assert p.title == "Senior AI Engineer"
    assert p.company == "Pacaso"
    assert "agent workflows" in p.description


def test_lever_fetcher_handles_http_error():
    with patch("agents.biz_dev_outreach.tools.httpx.get", side_effect=Exception("boom")), \
         patch("agents.biz_dev_outreach.tools.time.sleep"):
        postings = fetch_lever_board(["pacaso"])
    assert postings == []


def test_greenhouse_fetcher_parses_jobs():
    fake_payload = {
        "jobs": [
            {
                "id": 9876,
                "title": "AI Platform Engineer",
                "absolute_url": "https://boards.greenhouse.io/opendoor/jobs/9876",
                "content": "<p>Build <b>AI</b> platforms for ops automation.</p>",
                "location": {"name": "Remote — North America"},
                "updated_at": "2026-04-15T12:00:00Z",
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_payload
    mock_resp.raise_for_status = MagicMock()

    with patch("agents.biz_dev_outreach.tools.httpx.get", return_value=mock_resp), \
         patch("agents.biz_dev_outreach.tools.time.sleep"):
        postings = fetch_greenhouse_board(["opendoor"])

    assert len(postings) == 1
    p = postings[0]
    assert p.platform == "greenhouse"
    assert p.external_id == "9876"
    assert p.title == "AI Platform Engineer"
    assert "Build AI platforms" in p.description
    assert p.remote is True


# ─── Unit: Google Jobs / SerpAPI fetcher ─────────────────────────────────────


def test_google_jobs_fetcher_parses_results():
    fake_payload = {
        "jobs_results": [
            {
                "job_id": "eyJqb2JfdGl0bGUiOiJBSSBDb25zdWx0YW50In0=",
                "title": "AI Consultant — Ops Automation",
                "company_name": "Pacaso",
                "location": "Remote, United States",
                "description": "Build AI workflows for our operations automation team.",
                "detected_extensions": {
                    "schedule_type": "Contractor",
                    "posted_at": "3 days ago",
                },
                "apply_options": [{"link": "https://jobs.pacaso.com/ai-consultant"}],
            },
            {
                "job_id": "skip_this",
                "title": "Full-time ML Engineer",
                "company_name": "Acme",
                "location": "San Francisco, CA",
                "description": "Full-time position.",
                "detected_extensions": {"schedule_type": "Full time"},
                "apply_options": [],
            },
        ]
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_payload
    mock_resp.raise_for_status = MagicMock()

    with patch("agents.biz_dev_outreach.tools.httpx.get", return_value=mock_resp), \
         patch("agents.biz_dev_outreach.tools.time.sleep"), \
         patch("agents.biz_dev_outreach.tools.settings") as mock_settings:
        mock_settings.serp_api_key = "test_key"
        postings = fetch_google_jobs(["AI consultant ops automation remote"])

    # Full-time role must be filtered out; only contract role returned
    assert len(postings) == 1
    p = postings[0]
    assert p.platform == "google_jobs"
    assert p.title == "AI Consultant — Ops Automation"
    assert p.company == "Pacaso"
    assert p.remote is True
    assert "operations automation" in p.description


def test_google_jobs_skips_when_no_key():
    with patch("agents.biz_dev_outreach.tools.settings") as mock_settings:
        mock_settings.serp_api_key = ""
        postings = fetch_google_jobs(["AI consultant"])
    assert postings == []


def test_google_jobs_handles_http_error():
    with patch("agents.biz_dev_outreach.tools.httpx.get", side_effect=Exception("timeout")), \
         patch("agents.biz_dev_outreach.tools.time.sleep"), \
         patch("agents.biz_dev_outreach.tools.settings") as mock_settings:
        mock_settings.serp_api_key = "test_key"
        postings = fetch_google_jobs(["AI consultant"])
    assert postings == []
