"""Asyncpg LISTEN/NOTIFY listener for `dossier.ready` events.

Postgres fires `pg_notify('dossier_ready', ...)` when a row is inserted
into `research_dossiers`. This listener catches that notification and
dispatches the `strategic-arch` agent via the run lifecycle.

Why this instead of (or in addition to) broker BackgroundTasks:
  * Push-based: no polling, fires within ~10ms of the INSERT
  * Survives broker outages — the NOTIFY comes directly from Postgres
  * The existing broker still routes DOSSIER_READY to any other subscribers

Requires the migration at:
  omerion/infra/supabase/migrations/0063_dossier_notify_trigger.sql
"""
from __future__ import annotations

import asyncio
import json
import os

from omerion_core.logging import get_logger

log = get_logger("omerion.events.dossier_listener")

_CHANNEL = "dossier_ready"


class DossierListener:
    """Long-lived asyncpg connection that listens for dossier_ready notifications."""

    def __init__(self) -> None:
        self._conn = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        db_url = os.environ.get("SUPABASE_DB_URL")
        if not db_url:
            log.warning(
                "dossier_listener_skipped_no_db_url",
                hint="Set SUPABASE_DB_URL (direct Postgres URL) to enable push delivery",
            )
            return
        try:
            import asyncpg

            self._conn = await asyncpg.connect(db_url)
            await self._conn.execute(f"LISTEN {_CHANNEL};")
            log.info("dossier_listener_started", channel=_CHANNEL)
            self._task = asyncio.create_task(self._loop())
        except Exception as exc:  # noqa: BLE001
            log.error("dossier_listener_start_failed", error=str(exc))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._conn:
            try:
                await self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        log.info("dossier_listener_stopped")

    async def _loop(self) -> None:
        try:
            async for notification in self._conn:
                await self._handle(notification)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.error("dossier_listener_loop_error", error=str(exc))

    async def _handle(self, notification) -> None:
        try:
            payload = json.loads(notification.payload)
        except (json.JSONDecodeError, TypeError):
            log.warning("dossier_listener_bad_payload", raw=str(notification.payload)[:200])
            return

        dossier_id = payload.get("dossier_id") or payload.get("id")
        account_id = payload.get("account_id")
        log.info("dossier_ready_received", dossier_id=dossier_id, account_id=account_id)
        asyncio.create_task(self._dispatch_strategic_arch(dossier_id, account_id))

    async def _dispatch_strategic_arch(
        self, dossier_id: str | None, account_id: str | None
    ) -> None:
        try:
            from omerion_core.runtime import run_lifecycle
            from omerion_core.runtime.run_executor import execute_run

            run = run_lifecycle.create_run(
                agent_name="strategic-arch",
                source_channel="dossier_listener",
                inputs={"dossier_id": dossier_id, "account_id": account_id},
                triggered_by="asyncpg:dossier_ready",
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, execute_run, run["run_id"])
            log.info("dossier_strategic_arch_dispatched", run_id=run["run_id"])
        except Exception as exc:  # noqa: BLE001
            log.error("dossier_dispatch_failed", dossier_id=dossier_id, error=str(exc))
