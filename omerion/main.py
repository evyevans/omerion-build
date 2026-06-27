"""Omerion local runtime entrypoint.

    uv run uvicorn main:app --reload

Boots:
  * FastAPI (inbound/app.py) — /hitl/*, /webhooks/*, /health/*, /agents/*
  * LangGraph AsyncPostgresSaver (async; initialized via setup_checkpointer)
  * APScheduler (skill frontmatter-driven cron)
  * All 14 agent registrations (imported via omerion_core.runtime.bootstrap)
"""
from __future__ import annotations

import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()

from contextlib import asynccontextmanager

from omerion_core.inbound.app import app as inbound_app
from omerion_core.logging import get_logger
from omerion_core.clients.pinecone_client import close_async_index, init_async_index
from omerion_core.events.dossier_listener import DossierListener
from omerion_core.runtime.checkpointer import setup_checkpointer, teardown_checkpointer
from omerion_core.runtime.scheduler import start_scheduler

log = get_logger("omerion.main")

_scheduler = None
_broker_channel = None
_dossier_listener: DossierListener | None = None


@asynccontextmanager
async def lifespan(app):  # type: ignore[no-untyped-def]
    global _scheduler, _broker_channel, _dossier_listener
    # Import triggers agent registration side-effects.
    import agents  # noqa: F401

    await setup_checkpointer()  # async; logs warning if DATABASE_URL is unset
    await init_async_index()  # non-blocking; logs warning if PINECONE_API_KEY unset

    # Emit structured startup warnings for Growth dept vars that silently degrade.
    from omerion_core.settings import validate_growth_agent_settings
    for w in validate_growth_agent_settings():
        log.warning("growth_agent_config_warning", message=w)

    _scheduler = start_scheduler()

    # Wave 3.2: register the sweeper jobs (stuck runs, expired HITL, DLQ
    # retries). Best-effort — a registration failure is logged but does not
    # abort boot, because the rest of the system can still serve traffic
    # without the janitor; we'd rather discover the missing sweeper from
    # logs than refuse to start.
    try:
        from omerion_core.runtime.sweeper import register_sweeper_jobs
        register_sweeper_jobs(_scheduler)
    except Exception as exc:  # noqa: BLE001
        log.warning("sweeper_registration_failed", error=str(exc))

    # Wave 3.5: Mission Control alerts (cost spike, error rate, HITL
    # backlog, stuck-run rate). 15-minute tick — see module docstring.
    try:
        from omerion_core.runtime.mission_control_alerts import register_alert_jobs
        register_alert_jobs(_scheduler)
    except Exception as exc:  # noqa: BLE001
        log.warning("mission_control_alerts_registration_failed", error=str(exc))

    # Register cross-channel stop propagation: when a contact replies, all
    # active nurture sequences + queued LinkedIn messages are cancelled.
    try:
        from omerion_core.outreach.tracker import register_reply_listener
        register_reply_listener()
    except Exception as exc:  # noqa: BLE001
        log.warning("reply_listener_registration_failed", error=str(exc))

    # Wire the event broker: subscribe to all inter-agent handoff events so
    # downstream agents are triggered automatically when upstream agents emit.
    try:
        from omerion_core.events.broker import start_broker
        _broker_channel = start_broker()
    except Exception as exc:  # noqa: BLE001
        log.warning("event_broker_start_failed", error=str(exc))

    # asyncpg push listener for dossier.ready → strategic-arch (low-latency complement to broker)
    try:
        _dossier_listener = DossierListener()
        await _dossier_listener.start()
    except Exception as exc:  # noqa: BLE001
        log.warning("dossier_listener_start_failed", error=str(exc))

    log.info("omerion_runtime_booted")
    try:
        yield
    finally:
        if _dossier_listener is not None:
            try:
                await _dossier_listener.stop()
            except Exception:  # noqa: BLE001
                pass
        if _broker_channel is not None:
            try:
                _broker_channel.unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        await close_async_index()
        await teardown_checkpointer()
        if _scheduler is not None:
            _scheduler.shutdown(wait=False)
            log.info("omerion_runtime_shutdown")


# Reuse the inbound FastAPI app; attach our lifespan.
app = inbound_app
app.router.lifespan_context = lifespan

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
