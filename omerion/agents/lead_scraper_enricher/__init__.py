"""Lead Scraper & Enricher (Agent #3).

Wave 1.9 + 2.6 migration: wrapper contract + first-time HITL helper.
The graph can call `tools.first_time_account_ids(cohort)` to detect
never-before-enriched accounts and gate them through HITL before any
LinkedIn / Firecrawl scraping happens.
"""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("lead-scraper", runtime="langgraph", handler=_build())
