"""
OMERION Backbone — Fit Score Calculator (A3)
=============================================
100% deterministic. Zero AI dependency.
5 weighted lookup tables → integer 0–100.

Formula:
  fit_score = (company_size × 0.25) + (tech_maturity × 0.20)
            + (industry_match × 0.20) + (title_seniority × 0.20)
            + (geographic_fit × 0.15)
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class FitScoreBreakdown:
    company_size: int
    tech_maturity: int
    industry_match: int
    title_seniority: int
    geographic_fit: int
    total: int

    def to_dict(self) -> dict:
        return {
            "company_size": self.company_size,
            "tech_maturity": self.tech_maturity,
            "industry_match": self.industry_match,
            "title_seniority": self.title_seniority,
            "geographic_fit": self.geographic_fit,
            "total": self.total,
        }


# ── Sub-score lookup tables ──────────────────────────────────────────────────

def _score_company_size(head_count: int | str | None) -> int:
    """1-5 → 40 | 6-20 → 70 | 21-50 → 90 | 51+ → 100 | Unknown → 50"""
    if not head_count or str(head_count).lower() in ("unknown", "", "none"):
        return 50
    n = _parse_headcount(head_count)
    if n is None:
        return 50
    if n <= 5:
        return 40
    if n <= 20:
        return 70
    if n <= 50:
        return 90
    return 100


def _parse_headcount(value: int | str) -> int | None:
    """Parse '11-50', '50+', '~25', or plain int."""
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    # Handle ranges like "11-50" → take midpoint
    range_match = re.match(r"(\d+)\s*[-–]\s*(\d+)", s)
    if range_match:
        lo, hi = int(range_match.group(1)), int(range_match.group(2))
        return (lo + hi) // 2
    # Handle "50+" or "50 +"
    plus_match = re.match(r"(\d+)\s*\+", s)
    if plus_match:
        return int(plus_match.group(1))
    # Handle "~25"
    approx_match = re.match(r"[~≈]?\s*(\d+)", s)
    if approx_match:
        return int(approx_match.group(1))
    return None


def _score_tech_maturity(tech_stack: str | None) -> int:
    """No tools → 100 (high need) | Modern automation stack → 30 (already equipped)"""
    if not tech_stack or str(tech_stack).lower() in ("unknown", "", "none"):
        return 65  # midpoint when unknown
    stack = str(tech_stack).lower()
    modern_tools = [
        "salesforce", "hubspot", "pipedrive",
        "monday.com", "monday", "asana", "notion", "clickup",
        "intercom", "freshdesk", "zendesk",
    ]
    if any(tool in stack for tool in modern_tools):
        return 30  # already has modern tooling
    low_tech_signals = [
        "spreadsheet", "manual", "excel", "paper",
        "google sheets", "no crm", "none",
    ]
    if any(sig in stack for sig in low_tech_signals):
        return 100  # maximum automation need
    return 65  # unclear


def _score_industry_match(industry: str | None) -> int:
    """Tech-forward or automation-receptive industry → 100 | All commercial → 75 | Excluded → 20"""
    if not industry or str(industry).lower() in ("unknown", "", "none"):
        return 50
    ind = str(industry).lower()

    # Highest fit: industries with proven AI/automation ROI and active spend
    high_fit = [
        "software", "saas", "technology", "fintech", "insurtech",
        "marketing", "advertising", "media", "e-commerce", "ecommerce",
        "logistics", "supply chain", "staffing", "recruiting", "hr",
        "consulting", "professional services", "legal", "accounting",
        "finance", "agency", "manufacturing",
    ]
    if any(k in ind for k in high_fit):
        return 100

    # Low fit: industries resistant to AI automation or outside ICP
    excluded = [
        "government", "public sector", "military", "defense",
        "non-profit", "nonprofit", "charity",
    ]
    if any(k in ind for k in excluded):
        return 20

    # All other commercial industries — neutral score
    return 75


def _score_title_seniority(title: str | None) -> int:
    """Owner/C-Suite → 100 | Director → 80 | Manager → 60 | IC → 30"""
    if not title or str(title).lower() in ("unknown", "", "none"):
        return 50
    t = str(title).lower()
    c_suite = [
        "owner", "ceo", "coo", "cto", "cfo", "founder",
        "co-founder", "principal",
    ]
    if any(k in t for k in c_suite):
        return 100
    director = [
        "director", "vp", "vice president", "head of",
        "chief", "svp", "evp", "partner",
    ]
    if any(k in t for k in director):
        return 80
    manager = [
        "manager", "team lead", "team leader", "supervisor",
        "coordinator",
    ]
    if any(k in t for k in manager):
        return 60
    return 30


_US_STATE_ABBREVS = frozenset({
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
})


def _score_geographic_fit(location: str | None) -> int:
    """US → 100 | Canada → 80 | Other English → 50 | Other → 20"""
    if not location or str(location).lower() in ("unknown", "", "none"):
        return 50
    loc = str(location).lower()
    # US signals
    if "united states" in loc or "usa" in loc or "u.s." in loc:
        return 100
    # Check state abbreviations (", TX" or ", CA" patterns)
    state_match = re.search(r",\s*([a-z]{2})\b", loc)
    if state_match and state_match.group(1) in _US_STATE_ABBREVS:
        return 100
    # US city names (common)
    us_cities = [
        "new york", "los angeles", "chicago", "houston", "phoenix",
        "dallas", "miami", "atlanta", "denver", "seattle", "austin",
        "san francisco", "san diego", "las vegas", "nashville",
        "charlotte", "tampa", "orlando", "portland", "raleigh",
    ]
    if any(c in loc for c in us_cities):
        return 100
    # Canada
    canada_signals = [
        "canada", "ontario", "toronto", "vancouver", "calgary",
        "montreal", "ottawa", "edmonton", "winnipeg", "cambridge",
    ]
    if any(c in loc for c in canada_signals):
        return 80
    # Other English-speaking
    english_signals = [
        "uk", "united kingdom", "london", "australia", "sydney",
        "melbourne", "new zealand", "ireland", "dublin",
    ]
    if any(c in loc for c in english_signals):
        return 50
    return 20


# ── Main calculator ──────────────────────────────────────────────────────────

def calculate_fit_score(
    head_count: int | str | None = None,
    tech_stack: str | None = None,
    industry: str | None = None,
    title: str | None = None,
    location: str | None = None,
) -> FitScoreBreakdown:
    """Calculate fit_score from 5 deterministic lookup tables.

    Returns a FitScoreBreakdown with individual sub-scores and weighted total.
    """
    cs = _score_company_size(head_count)
    tm = _score_tech_maturity(tech_stack)
    im = _score_industry_match(industry)
    ts = _score_title_seniority(title)
    gf = _score_geographic_fit(location)

    total = round(
        (cs * 0.25) + (tm * 0.20) + (im * 0.20) + (ts * 0.20) + (gf * 0.15)
    )

    return FitScoreBreakdown(
        company_size=cs,
        tech_maturity=tm,
        industry_match=im,
        title_seniority=ts,
        geographic_fit=gf,
        total=total,
    )
