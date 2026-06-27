"""APScheduler wrapper — reads `schedule:` frontmatter from each .skill.md.

Skills own their cadence via `schedule:` frontmatter. The scheduler runs
inside the FastAPI process — no external gateway dependency.

Each frontmatter `schedule:` is a cron string in `America/Toronto`. Skills
with `trigger: event` or `trigger: webhook` are ignored here — those fire
through `inbound/` routes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from omerion_core.logging import get_logger
from omerion_core.runtime.registry import get_handler
from omerion_core.settings import settings

log = get_logger("omerion.runtime.scheduler")

SKILLS_DIR = Path("skills")
DEFAULT_TZ = "America/Toronto"
JITTER_SECONDS = 30


def _load_skill_meta(path: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter of a .skill.md file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---\n")
    fm, _, _ = rest.partition("\n---")
    return yaml.safe_load(fm) or {}


async def _cleanup_checkpoints() -> None:
    """Nightly checkpoint TTL cleanup (2:30 AM ET).

    Must be module-level (not nested inside start_scheduler) so APScheduler's
    SQLAlchemyJobStore can pickle and restore it across process restarts.
    """
    from omerion_core.runtime.checkpointer import cleanup_expired_checkpoints_async
    result = await cleanup_expired_checkpoints_async()
    log.info("checkpoint_cleanup_scheduled", **result)


async def _run_skill(name: str) -> None:
    """AsyncIOScheduler job — invoked by cron via the FastAPI event loop.

    Async so it can await execute_run_async() which uses asyncio.wait_for +
    graph.ainvoke() for clean 30-minute timeout propagation via CancelledError.
    """
    from uuid import uuid4

    from omerion_core.runtime import run_lifecycle
    from omerion_core.runtime.run_executor import execute_run_async

    try:
        get_handler(name)
    except KeyError:
        log.error("scheduled_skill_not_registered", skill=name)
        return

    run_id = str(uuid4())
    try:
        run_lifecycle.create_run(
            run_id=run_id,
            agent_name=name,
            source_channel="scheduler",
            inputs={"session_id": run_id, "run_id": run_id, "triggered_by": "scheduler"},
            triggered_by="cron",
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("scheduled_skill_create_run_failed", skill=name, error=str(exc))
        return

    try:
        await execute_run_async(run_id)
        log.info("scheduled_skill_completed", skill=name, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 — one skill failure must not crash the scheduler
        log.exception("scheduled_skill_failed", skill=name, run_id=run_id, error=str(exc))


def start_scheduler(*, skills_dir: Path | None = None) -> AsyncIOScheduler:
    jobstores: dict = {}
    if settings.database_url:
        try:
            from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
            jobstores["default"] = SQLAlchemyJobStore(url=settings.database_url)
            log.info("scheduler_persistent_jobstore", url=settings.database_url[:40] + "...")
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler_jobstore_fallback_memory", error=str(exc))

    scheduler = AsyncIOScheduler(jobstores=jobstores if jobstores else {}, timezone=DEFAULT_TZ)
    root = skills_dir or SKILLS_DIR
    if not root.exists():
        log.warning("skills_dir_missing", path=str(root))
        scheduler.start()
        return scheduler

    for skill_file in sorted(root.glob("*.skill.md")):
        meta = _load_skill_meta(skill_file)
        schedule = meta.get("schedule")
        trigger = meta.get("trigger", "cron")
        skill_name = skill_file.stem.removesuffix(".skill")

        if trigger != "cron" or not schedule:
            log.info("skill_skipped_non_cron", skill=skill_name, trigger=trigger)
            continue

        try:
            cron = CronTrigger.from_crontab(schedule, timezone=DEFAULT_TZ)
        except Exception as exc:  # noqa: BLE001
            log.error("skill_cron_parse_failed", skill=skill_name, schedule=schedule, error=str(exc))
            continue

        scheduler.add_job(
            _run_skill,
            trigger=cron,
            args=[skill_name],
            id=f"skill:{skill_name}",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("skill_scheduled", skill=skill_name, schedule=schedule)

    # ── Daily digest via Discord webhook (08:00 + 18:00 Toronto time) ──
    for cron_str in ("0 8 * * *", "0 18 * * *"):
        tag = cron_str.split()[1]  # "8" or "18"
        try:
            cron = CronTrigger.from_crontab(cron_str, timezone=DEFAULT_TZ)
            scheduler.add_job(
                _post_daily_digest,
                trigger=cron,
                id=f"daily-digest-{tag}",
                jitter=JITTER_SECONDS,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
            log.info("daily_digest_scheduled", time=f"{tag}:00")
        except Exception as exc:  # noqa: BLE001
            log.error("daily_digest_schedule_failed", error=str(exc))

    # ── Response Tracker (every 2h) — detect Gmail replies to outbound comms ──
    try:
        from omerion_core.outreach.tracker import run_response_tracker
        scheduler.add_job(
            run_response_tracker,
            trigger=CronTrigger.from_crontab("0 */2 * * *", timezone=DEFAULT_TZ),
            id="response-tracker",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("response_tracker_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("response_tracker_schedule_failed", error=str(exc))

    # ── Ghost Detector (daily 07:00 ET) — escalate non-responding contacts ──
    try:
        from omerion_core.outreach.ghost_detector import run_ghost_detector
        scheduler.add_job(
            run_ghost_detector,
            trigger=CronTrigger.from_crontab("0 7 * * *", timezone=DEFAULT_TZ),
            id="ghost-detector",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("ghost_detector_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("ghost_detector_schedule_failed", error=str(exc))

    # ── Drive Channel Renewal (daily 03:00 ET) — re-register expiring watch channels ──
    try:
        from pipeline.watcher import renew_drive_channels
        scheduler.add_job(
            renew_drive_channels,
            trigger=CronTrigger.from_crontab("0 3 * * *", timezone=DEFAULT_TZ),
            id="drive-channel-renewal",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("drive_channel_renewal_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("drive_channel_renewal_schedule_failed", error=str(exc))

    # ── Deployment Watchdog (every 15 min) — alert on stalled deployments ──
    # Wave 7.0 (Musk audit blocker #2): registers the previously-orphan
    # scripts/deployment_watchdog.py so its check_stalled_deployments
    # function actually runs in production.
    try:
        from scripts.deployment_watchdog import check_stalled_deployments
        scheduler.add_job(
            check_stalled_deployments,
            trigger=CronTrigger.from_crontab("*/15 * * * *", timezone=DEFAULT_TZ),
            id="deployment-watchdog",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("deployment_watchdog_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("deployment_watchdog_schedule_failed", error=str(exc))

    # ── R4 Regression Alert (daily 02:00 ET) — auto-pause on critical regressions ──
    # Replaces the retired r4_evaluation_telemetry agent.
    try:
        from scripts.r4_regression_alert import run_once as r4_run_once
        scheduler.add_job(
            r4_run_once,
            trigger=CronTrigger.from_crontab("0 2 * * *", timezone=DEFAULT_TZ),
            id="r4-regression-alert",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("r4_regression_alert_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("r4_regression_alert_schedule_failed", error=str(exc))

    # ── Competitive Intel (daily 04:00 ET) — RSS fetch + Pinecone upsert ──
    # Replaces the retired competitive_intel agent.
    try:
        from scripts.competitive_intel_cron import run_once as competitive_intel_run_once
        scheduler.add_job(
            competitive_intel_run_once,
            trigger=CronTrigger.from_crontab("0 4 * * *", timezone=DEFAULT_TZ),
            id="competitive-intel",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("competitive_intel_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("competitive_intel_schedule_failed", error=str(exc))

    # ── Proactive Lead Research (TWATR-T) — daily deep-research on priority accounts ──
    # Cadence is config-driven (config/agents.yaml: high_quality_lead_scraping.schedule).
    # Enqueues through the run lifecycle so HITL + cost watchdog apply like a Discord run.
    try:
        from omerion_core.settings import settings as _settings
        sched_cfg = (_settings.agent("high_quality_lead_scraping") or {}).get("schedule") or {}
        if sched_cfg.get("enabled") and sched_cfg.get("cron"):
            scheduler.add_job(
                _enqueue_proactive_lead_research,
                trigger=CronTrigger.from_crontab(sched_cfg["cron"], timezone=DEFAULT_TZ),
                id="proactive-lead-research",
                jitter=JITTER_SECONDS,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
            log.info("proactive_lead_research_scheduled", cron=sched_cfg["cron"])
    except Exception as exc:  # noqa: BLE001
        log.error("proactive_lead_research_schedule_failed", error=str(exc))

    # ── Daily Nurture sweep (TWATR-T) — re-touch contacts due per cooldown ──
    # Config-driven (config/agents.yaml: crm_nurture.schedule). Goes through the
    # run lifecycle so the G1 send-gate + cost watchdog apply like a Discord run.
    try:
        from omerion_core.settings import settings as _settings
        nurture_cfg = (_settings.agent("crm_nurture") or {}).get("schedule") or {}
        if nurture_cfg.get("enabled") and nurture_cfg.get("cron"):
            scheduler.add_job(
                _enqueue_daily_nurture,
                trigger=CronTrigger.from_crontab(nurture_cfg["cron"], timezone=DEFAULT_TZ),
                id="daily-nurture-sweep",
                jitter=JITTER_SECONDS,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
            log.info("daily_nurture_scheduled", cron=nurture_cfg["cron"])
    except Exception as exc:  # noqa: BLE001
        log.error("daily_nurture_schedule_failed", error=str(exc))

    # ── Weekly Market Map (TWATR-T) — autonomous account discovery ──
    # Config-driven (config/agents.yaml: market_mapper.schedule). Lifecycle path so
    # downstream ACCOUNT_BATCH_READY → enricher (G2-gated) flows like any run.
    try:
        from omerion_core.settings import settings as _settings
        mm_cfg = (_settings.agent("market_mapper") or {}).get("schedule") or {}
        if mm_cfg.get("enabled") and mm_cfg.get("cron"):
            scheduler.add_job(
                _enqueue_weekly_market_map,
                trigger=CronTrigger.from_crontab(mm_cfg["cron"], timezone=DEFAULT_TZ),
                id="weekly-market-map",
                jitter=JITTER_SECONDS,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
            log.info("weekly_market_map_scheduled", cron=mm_cfg["cron"])
    except Exception as exc:  # noqa: BLE001
        log.error("weekly_market_map_schedule_failed", error=str(exc))

    # ── Nightly Audit sweep (TWATR-T) — constitutional compliance scan ──
    # Config-driven (config/agents.yaml: auditor.schedule). Lifecycle path so HITL
    # (suspicious verdicts / weekly report) + cost watchdog apply like any run.
    try:
        from omerion_core.settings import settings as _settings
        audit_cfg = (_settings.agent("auditor") or {}).get("schedule") or {}
        if audit_cfg.get("enabled") and audit_cfg.get("cron"):
            scheduler.add_job(
                _enqueue_nightly_audit,
                trigger=CronTrigger.from_crontab(audit_cfg["cron"], timezone=DEFAULT_TZ),
                id="nightly-audit-sweep",
                jitter=JITTER_SECONDS,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
            log.info("nightly_audit_scheduled", cron=audit_cfg["cron"])
    except Exception as exc:  # noqa: BLE001
        log.error("nightly_audit_schedule_failed", error=str(exc))

    # ── Newsletter Skill Packs (Every 2 weeks on Tuesday) ──
    try:
        scheduler.add_job(
            _enqueue_newsletter_skillpack,
            trigger=CronTrigger.from_crontab("0 9 1,15 * *", timezone=DEFAULT_TZ),
            id="newsletter-skillpack",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("newsletter_skillpack_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("newsletter_skillpack_schedule_failed", error=str(exc))

    # ── Newsletter Playbooks (Monthly on the 1st) ──
    try:
        scheduler.add_job(
            _enqueue_newsletter_playbook,
            trigger=CronTrigger.from_crontab("0 9 1 * *", timezone=DEFAULT_TZ),
            id="newsletter-playbook",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("newsletter_playbook_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("newsletter_playbook_schedule_failed", error=str(exc))

    # ── Weekly Newsletter (Mondays 09:00) ──
    try:
        scheduler.add_job(
            _enqueue_newsletter_weekly,
            trigger=CronTrigger.from_crontab("0 9 * * 1", timezone=DEFAULT_TZ),
            id="newsletter-weekly",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("newsletter_weekly_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("newsletter_weekly_schedule_failed", error=str(exc))

    # ── Daily Attribution fan-out (TWATR-T) — one run per matured deployment ──
    # Config-driven (config/agents.yaml: outcome_attribution.schedule). The graph
    # is per-deployment, so the generic skill scheduler can't drive it — this job
    # enumerates due deployments and fans out one lifecycle run each.
    try:
        from omerion_core.settings import settings as _settings
        attr_cfg = (_settings.agent("outcome_attribution") or {}).get("schedule") or {}
        if attr_cfg.get("enabled") and attr_cfg.get("cron"):
            scheduler.add_job(
                _enqueue_due_attributions,
                trigger=CronTrigger.from_crontab(attr_cfg["cron"], timezone=DEFAULT_TZ),
                id="daily-attribution-fanout",
                jitter=JITTER_SECONDS,
                coalesce=True,
                max_instances=1,
                replace_existing=True,
            )
            log.info("daily_attribution_scheduled", cron=attr_cfg["cron"])
    except Exception as exc:  # noqa: BLE001
        log.error("daily_attribution_schedule_failed", error=str(exc))

    # ── HITL SLA alert (every 6h) — remind founder of reviews pending > 24h ──
    try:
        scheduler.add_job(
            _check_stale_hitl_reviews,
            trigger=CronTrigger.from_crontab("0 */6 * * *", timezone=DEFAULT_TZ),
            id="hitl-sla-alert",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("hitl_sla_alert_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("hitl_sla_alert_schedule_failed", error=str(exc))

    # ── R3 Coordination Safety Net (Tue 10:00 ET) ──
    # Event-driven trigger fires R3 as soon as R1+R2 complete. This is the fallback
    # in case mark_agent_complete was never called (agent crashed, etc.).
    try:
        from omerion_core.runtime.agent_coordinator import check_r3_gate
        scheduler.add_job(
            check_r3_gate,
            trigger=CronTrigger.from_crontab("0 10 * * 2", timezone=DEFAULT_TZ),
            id="r3-gate-safety-net",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("r3_gate_safety_net_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("r3_gate_safety_net_failed", error=str(exc))

    scheduler.start()
    log.info("scheduler_started", job_count=len(scheduler.get_jobs()))

    scheduler.add_job(
        _cleanup_checkpoints,
        trigger=CronTrigger(hour=2, minute=30, timezone=DEFAULT_TZ),
        id="checkpointer.ttl_cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log.info("checkpoint_ttl_cleanup_scheduled")

    # ── Discord bot liveness watchdog (every 5 min) ──
    # The bot is a SEPARATE Railway service. Nothing previously noticed when it
    # died — it was offline ~3 weeks before anyone saw Discord wasn't responding.
    # This pings the bot's /health endpoint and alerts #mission-control if down.
    try:
        scheduler.add_job(
            _check_bot_liveness,
            trigger=CronTrigger.from_crontab("*/5 * * * *", timezone=DEFAULT_TZ),
            id="discord-bot-liveness",
            jitter=JITTER_SECONDS,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
        log.info("bot_liveness_watchdog_scheduled")
    except Exception as exc:  # noqa: BLE001
        log.error("bot_liveness_watchdog_schedule_failed", error=str(exc))

    # ── Trigger contract validation (Phase 3 — TWATR-T) ──
    # Cross-reference skill.md trigger declarations against broker/scheduler/discord
    # wiring. Logs warnings on drift; never blocks startup.
    try:
        from omerion_core.runtime.trigger_validation import validate_trigger_contracts
        validate_trigger_contracts(skills_dir=root)
    except Exception as exc:  # noqa: BLE001
        log.warning("trigger_validation_failed", error=str(exc))

    return scheduler


async def _check_bot_liveness() -> None:
    """Watchdog: alert #mission-control if the Discord bot is down/stale.

    The bot's health sidecar (health_sidecar.serve_health) exposes /health on
    BOT_HEALTH_PORT and returns ok=false / HTTP 503 when its heartbeat is stale.
    We GET it at BOT_HEALTH_URL (the bot service's internal URL). Disabled (no-op)
    until BOT_HEALTH_URL is set, so local/dev runs don't false-alarm. Best-effort;
    send_alert is hour-bucketed so a sustained outage pages at most once/hour.
    """
    import os

    url = os.getenv("BOT_HEALTH_URL", "")
    if not url:
        return  # not configured → watchdog disabled
    healthy = False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(url)
        healthy = resp.status_code == 200 and bool((resp.json() or {}).get("ok"))
    except Exception as exc:  # noqa: BLE001 — unreachable bot counts as down
        log.warning("bot_liveness_check_failed", url=url, error=str(exc))
        healthy = False
    if not healthy:
        log.error("discord_bot_down", url=url)
        try:
            from omerion_core.runtime.mission_control_alerts import send_alert

            send_alert(
                "bot_down",
                "🔴 **Discord bot is DOWN** — channel triggers are not being forwarded. "
                "Check the `omerion-discord-bot` Railway service.",
            )
        except Exception as exc:  # noqa: BLE001
            log.error("bot_down_alert_failed", error=str(exc))


async def _enqueue_proactive_lead_research() -> None:
    """Daily proactive run of high-quality lead scraping over priority accounts."""
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run_async

        run = run_lifecycle.create_run(
            agent_name="hq-lead-scraping",
            source_channel="scheduler",
            inputs={"mode": "proactive"},
            triggered_by="scheduler:daily-lead-research",
        )
        log.info("proactive_lead_research_enqueued", run_id=run["run_id"])
        await execute_run_async(run["run_id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("proactive_lead_research_failed", error=str(exc))


async def _enqueue_weekly_market_map() -> None:
    """Weekly autonomous account discovery (MARKET MAPPER)."""
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run_async

        run = run_lifecycle.create_run(
            agent_name="market-mapper",
            source_channel="scheduler",
            inputs={},
            triggered_by="scheduler:weekly-market-map",
        )
        log.info("weekly_market_map_enqueued", run_id=run["run_id"])
        await execute_run_async(run["run_id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("weekly_market_map_failed", error=str(exc))


async def _enqueue_due_attributions() -> None:
    """Daily fan-out for OUTCOME ATTRIBUTION (#10).

    The attribution graph runs on ONE `deployment_id` per session, so a plain
    cron tick can't drive it (its `deployment_id` state field is required). This
    job enumerates deployments whose post-window has matured
    (`go_live_date <= now - pre_post_window_days`) and that are still `live`, and
    fans out one lifecycle run per deployment. `write_report` upserts on
    `deployment_id`, so re-running is idempotent — but we skip deployments that
    already have a report to avoid daily re-processing. Never raises.
    """
    try:
        from datetime import datetime, timedelta, timezone

        from omerion_core.clients.supabase_client import supabase
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run_async
        from omerion_core.settings import settings as _settings

        cfg = _settings.agent("outcome_attribution") or {}
        window_days = int(cfg.get("pre_post_window_days", 30))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()

        deps = (
            supabase.table("deployments")
            .select("deployment_id,go_live_date,status")
            .eq("status", "live")
            .lte("go_live_date", cutoff)
            .execute()
            .data
        ) or []

        enqueued = 0
        for dep in deps:
            did = dep.get("deployment_id")
            if not did:
                continue
            # Skip deployments already attributed (a report row exists).
            existing = (
                supabase.table("attribution_reports")
                .select("report_id")
                .eq("deployment_id", did)
                .limit(1)
                .execute()
                .data
            )
            if existing:
                continue
            run = run_lifecycle.create_run(
                agent_name="outcome-attribution",
                source_channel="scheduler",
                inputs={"deployment_id": did},
                triggered_by="scheduler:due-attributions",
            )
            await execute_run_async(run["run_id"])
            enqueued += 1
        log.info("due_attributions_enqueued", count=enqueued, candidates=len(deps))
    except Exception as exc:  # noqa: BLE001
        log.exception("due_attributions_failed", error=str(exc))


async def _enqueue_nightly_audit() -> None:
    """Nightly constitutional compliance sweep (AUDITOR)."""
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run_async

        run = run_lifecycle.create_run(
            agent_name="auditor",
            source_channel="scheduler",
            inputs={"trigger_mode": "nightly_cron"},
            triggered_by="scheduler:nightly-audit-sweep",
        )
        log.info("nightly_audit_enqueued", run_id=run["run_id"])
        await execute_run_async(run["run_id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("nightly_audit_failed", error=str(exc))


async def _enqueue_newsletter_skillpack() -> None:
    """Trigger the newsletter generator for bi-weekly skill packs."""
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run_async

        run = run_lifecycle.create_run(
            agent_name="newsletter_generator",
            source_channel="scheduler",
            inputs={"mode": "skillpack"},
            triggered_by="scheduler:newsletter-skillpack",
        )
        log.info("newsletter_skillpack_enqueued", run_id=run["run_id"])
        await execute_run_async(run["run_id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("newsletter_skillpack_failed", error=str(exc))


async def _enqueue_newsletter_playbook() -> None:
    """Trigger the newsletter generator for monthly playbooks."""
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run_async

        run = run_lifecycle.create_run(
            agent_name="newsletter_generator",
            source_channel="scheduler",
            inputs={"mode": "playbook"},
            triggered_by="scheduler:newsletter-playbook",
        )
        log.info("newsletter_playbook_enqueued", run_id=run["run_id"])
        await execute_run_async(run["run_id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("newsletter_playbook_failed", error=str(exc))


async def _enqueue_newsletter_weekly() -> None:
    """Trigger the newsletter generator for the weekly newsletter."""
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run_async

        run = run_lifecycle.create_run(
            agent_name="newsletter_generator",
            source_channel="scheduler",
            inputs={"mode": "newsletter"},
            triggered_by="scheduler:newsletter-weekly",
        )
        log.info("newsletter_weekly_enqueued", run_id=run["run_id"])
        await execute_run_async(run["run_id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("newsletter_weekly_failed", error=str(exc))


async def _enqueue_daily_nurture() -> None:
    """Daily nurture sweep — re-touch warm contacts due per their stage cooldown."""
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run_async

        run = run_lifecycle.create_run(
            agent_name="crm-nurture",
            source_channel="scheduler",
            inputs={},
            triggered_by="scheduler:daily-nurture-sweep",
        )
        log.info("daily_nurture_enqueued", run_id=run["run_id"])
        await execute_run_async(run["run_id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("daily_nurture_failed", error=str(exc))


def _check_stale_hitl_reviews() -> None:
    """Alert the founder about HITL reviews that have been pending for over 24 hours.

    Runs every 6 hours. Never raises — notification failures are swallowed so
    one bad webhook config doesn't crash the scheduler process.
    """
    try:
        from datetime import datetime, timedelta, timezone

        from omerion_core.clients.supabase_client import supabase
        from omerion_core.notifications.discord_webhook import post_hitl_alert
        from omerion_core.settings import settings

        if not settings.discord_hitl_webhook_url:
            log.info("hitl_sla_alert_skipped_no_webhook")
            return

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows = (
            supabase.table("founder_review_queue")
            .select("*")
            .eq("decision", "pending")
            .lt("created_at", cutoff)
            .execute()
        ).data or []

        for row in rows:
            try:
                base = settings.supabase_url or ""
                post_hitl_alert(
                    review_id=row["review_id"],
                    agent_name=row.get("agent_name", "unknown"),
                    subject=f"[REMINDER] {row.get('subject', 'Pending approval')}",
                    context_md=(
                        f"This review has been pending for more than 24 hours.\n\n"
                        f"{row.get('context_md', '')[:800]}"
                    ),
                    approve_url=f"{base}/hitl/resolve/{row['review_id']}?decision=approved",
                    reject_url=f"{base}/hitl/resolve/{row['review_id']}?decision=rejected",
                )
                log.info("hitl_sla_alert_sent", review_id=row["review_id"])
            except Exception as exc:  # noqa: BLE001
                log.warning("hitl_sla_alert_row_failed", review_id=row.get("review_id"), error=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("hitl_sla_alert_failed", error=str(exc))


def _post_daily_digest() -> None:
    """Fetch /reports/daily internally and post to Discord completion webhook."""
    try:
        from omerion_core.inbound.routes.control_plane import daily_report
        from omerion_core.notifications.discord_webhook import _post
        from omerion_core.settings import settings

        url = settings.discord_completion_webhook_url
        if not url:
            log.warning(
                "daily_digest_skipped_no_webhook",
                hint="Set DISCORD_COMPLETION_WEBHOOK_URL to enable daily digest posts",
            )
            return

        report = daily_report()
        embed = {
            "title": f"📊 Omerion Daily — {report.run_date}",
            "description": (
                f"**{report.headline}**\n\n"
                f"• Pending approvals: {report.pending_reviews}\n"
                f"• Runs (24h): {report.runs_last_24h}\n"
                f"• New accounts: {report.new_accounts_24h}\n"
                f"• New opportunities: {report.new_opportunities_24h}\n"
                f"• R4 regression alerts: {report.r4_alerts_24h}"
            ),
            "color": 0x3498DB,
        }
        _post(url, {"embeds": [embed]}, thread_id=None)
        log.info("daily_digest_posted")
    except Exception as exc:  # noqa: BLE001
        log.exception("daily_digest_failed", error=str(exc))
