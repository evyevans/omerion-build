"""Agents package init — imports all submodules to trigger registry side-effects.

Wave 0 retirements (3 agents → deterministic replacements; no LLM):
  * client_success            → omerion/scripts/replace_client_success.sql
                                (view: client_health_today)
  * competitive_intel         → omerion/scripts/competitive_intel_cron.py
                                (RSS + deterministic keyword tagging + Pinecone)
  * r4_evaluation_telemetry   → omerion/scripts/r4_regression_alert.py
                                (SQL rollup + auto-pause + Discord alert)

Wave 5 addition (2026-05-24): TRAINER, the 16th agent. Chief Intelligence
Officer that proposes weekly prompt improvements for the 6 wrapper-migrated
agents. See `omerion/skills/trainer.skill.md` for the spec.

Wave 6 addition (2026-05-24): DEPLOYER, the 18th agent. Agentic Factory
member that provisions infrastructure, runs migrations, smoke-tests the live
endpoint, and rolls back on failure. See `omerion/skills/deployer.skill.md`.

Wave 7 addition (2026-05-24): HEALER (RSI #16). Autonomous Remediation Engine
that closes the Health Loop — consumes regression.alert, diagnoses root cause,
patches config/skills, and emits healing.applied for AUDITOR to verify.
"""
from __future__ import annotations

from omerion_core.logging import get_logger

log = get_logger("omerion.agents")

# NOTE (2026-06-21): the following agents were migrated to the Anthropic
# managed-agents cloud platform (Claude Dev) and their local LangGraph copies
# removed: r1_market_tech_watcher, builder, deployer, validator, healer,
# auditor, qa_tester, trainer, client_onboarding, meeting_intelligence,
# outcome_attribution. See the migration plan for the full rationale.

_AGENT_MODULES = [
    "biz_dev_outreach",
    "crm_nurture",
    "high_quality_lead_scraping",
    "icp_scoring",
    "lead_scraper_enricher",
    "linkedin_outreach",
    "market_mapper",
    "offer_matching",
    "r2_oss_scout",
    "r3_strategic_architect",
    "factory_intake",
    "automation_strategist",
    "executive_polisher",
    "diagram_delivery",
    "client_intake",
    "spec_architect",
    "factory_rag",
    "client_comms",
    "newsletter_generator",
    "compliance_checker",
    "security_auditor",
]

_loaded: list[str] = []

for _mod in _AGENT_MODULES:
    try:
        __import__(f"agents.{_mod}")
        _loaded.append(_mod)
    except Exception as exc:  # noqa: BLE001 — one broken agent must not block API boot
        log.warning("agent_import_skipped", agent=_mod, error=str(exc))

if _loaded:
    log.info("agents_registered", count=len(_loaded), agents=_loaded)

__all__ = _loaded
