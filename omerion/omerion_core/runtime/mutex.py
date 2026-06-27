"""Distributed mutex with TTL-based stale-lock recovery.

Use this when more than one process or thread can fire the same cron / job /
synthesis run and you need at-most-one-winner semantics. The TTL on every
acquisition means a crashed holder does NOT block future acquirers forever —
the next caller after `ttl_seconds` will steal the lock atomically.

Typical use:

    from omerion_core.runtime.mutex import mutex
    holder = f"{platform.node()}:{os.getpid()}"
    with mutex("rsi_synthesis", ttl_seconds=3600, holder_id=holder) as acquired:
        if not acquired:
            log.info("rsi_synthesis_skipped_mutex_held")
            return
        _run_rsi_synthesis()

Backed by table `system_mutex` and SQL function `try_acquire_mutex` from
migration 0035. The SQL function does the atomic INSERT-or-steal-if-expired
in a single roundtrip; Python is a thin wrapper that confirms ownership by
holder_id and provides ergonomic context-management.
"""
from __future__ import annotations

import os
import platform
from contextlib import contextmanager
from typing import Iterator

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.runtime.mutex")


def default_holder_id() -> str:
    """Build a holder identifier that includes machine + process for debugging."""
    return f"{platform.node()}:{os.getpid()}"


def acquire_mutex(lock_name: str, ttl_seconds: int, holder_id: str) -> bool:
    """Try to acquire `lock_name` with a TTL. Returns True iff we now hold it.

    Atomic under concurrent callers: the SQL function `try_acquire_mutex`
    does the INSERT (or steal-if-expired UPDATE) under row lock and returns
    the resulting holder. We compare to our own holder_id to confirm ownership.
    """
    resp = supabase.rpc(
        "try_acquire_mutex",
        {
            "p_lock_name": lock_name,
            "p_ttl_seconds": ttl_seconds,
            "p_holder_id": holder_id,
        },
    ).execute()
    actual_holder = resp.data
    acquired = actual_holder == holder_id
    log.info(
        "mutex_acquire_attempt",
        lock_name=lock_name,
        holder_id=holder_id,
        acquired=acquired,
        actual_holder=actual_holder,
    )
    return acquired


def release_mutex(lock_name: str, holder_id: str) -> None:
    """Release `lock_name` only if we are the current holder.

    Holder-scoped DELETE prevents a worker from accidentally releasing
    another worker's lock (e.g., after this worker's lock has already
    expired and been stolen).
    """
    (
        supabase.table("system_mutex")
        .delete()
        .eq("lock_name", lock_name)
        .eq("acquired_by", holder_id)
        .execute()
    )
    log.info("mutex_released", lock_name=lock_name, holder_id=holder_id)


@contextmanager
def mutex(
    lock_name: str,
    ttl_seconds: int,
    holder_id: str | None = None,
) -> Iterator[bool]:
    """Context manager. Yields True if acquired, False if held by another.

    Always releases on exit when we hold the lock — even on exception. If we
    never acquired, release is skipped (so we don't delete someone else's row).
    """
    h = holder_id or default_holder_id()
    acquired = acquire_mutex(lock_name, ttl_seconds, h)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                release_mutex(lock_name, h)
            except Exception as exc:  # noqa: BLE001
                # Release failure is non-fatal — the TTL will reclaim the lock.
                log.warning("mutex_release_failed", lock_name=lock_name, error=str(exc))
