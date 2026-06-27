"""GitHub webhook handler — routes pull_request events to VALIDATOR.

Registers: POST /webhooks/github

Signature verification uses GITHUB_WEBHOOK_SECRET (HMAC-SHA256).
If the secret is not set, requests are accepted without verification
(dev-only; Railway must set the secret in prod).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from omerion_core.logging import get_logger

log = get_logger("omerion.inbound.github_webhook")

router = APIRouter()

_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")


def _verify_signature(body: bytes, sig_header: str | None) -> None:
    if not _WEBHOOK_SECRET:
        return  # dev: skip verification when secret not configured
    if not sig_header or not sig_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing GitHub signature")
    expected = "sha256=" + hmac.new(
        _WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")


async def _run_validator(payload: dict[str, Any]) -> None:
    """Background task: extract PR context and invoke VALIDATOR graph."""
    pr = payload.get("pull_request", {})
    pr_number: int | None = pr.get("number")
    pr_url: str = pr.get("html_url", "")
    head_branch: str = pr.get("head", {}).get("ref", "")
    repo_full: str = payload.get("repository", {}).get("full_name", "")

    if not all([pr_number, repo_full, head_branch]):
        log.warning("github_webhook_missing_fields", payload_keys=list(payload.keys()))
        return

    from omerion_core.runtime.mutex import acquire_mutex, release_mutex

    mutex_key = f"validator:pr:{repo_full}:{pr_number}"
    holder_id = str(uuid4())
    held = acquire_mutex(mutex_key, ttl_seconds=300, holder_id=holder_id)
    if not held:
        log.info("validator_mutex_held_skipping", pr=pr_number, key=mutex_key)
        return

    from agents.validator.graph import build
    from agents.validator.state import ValidatorState

    state = ValidatorState(
        pr_url=pr_url,
        pr_number=pr_number,
        repo_full=repo_full,
        head_branch=head_branch,
    )

    try:
        graph = build()
        graph.invoke(state)
    except Exception as exc:
        log.error("validator_graph_error", pr=pr_number, error=str(exc))
    finally:
        release_mutex(mutex_key, holder_id=holder_id)


@router.post("/webhooks/github", status_code=204)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
):
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    if x_github_event != "pull_request":
        return  # ignore push, issues, check_run, etc.

    payload: dict = await request.json()
    action = payload.get("action", "")

    if action not in ("opened", "synchronize"):
        return  # only trigger on new PR or new commits pushed

    log.info("github_webhook_received", action=action)
    background_tasks.add_task(_run_validator, payload)
