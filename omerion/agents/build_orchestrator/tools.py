"""Tools for the Build Orchestrator.

Talks to: Supabase (blueprints, build_tasks, deployments), GitHub
(issues, branches, PRs, CI checks), and Cursor/Antigravity via a local
injection shim. Heavy reasoning goes through the Claude router; coding
failures fall back to DeepSeek.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Iterable
from uuid import UUID

from github import GithubException

from omerion_core.clients.github_client import github_client
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.rate_limit.token_bucket import BUCKETS
from omerion_core.retry import transient_retry
from omerion_core.settings import settings

from .prompts import (
    CLIENT_DOC_SYSTEM,
    CLIENT_DOC_USER,
    DECOMPOSE_SYSTEM,
    DECOMPOSE_USER,
    ISSUE_BODY_SYSTEM,
    ISSUE_BODY_USER,
)
from .state import ClientDeliverable, TaskSpec

log = get_logger("omerion.agents.build_orchestrator")

_POLL_TIMEOUT_S = 1800  # 30 minutes — CI on Railway can be slow; prevents infinite block


# ─── Blueprint + task persistence ─────────────────────────────────────

def load_blueprint(blueprint_id: UUID) -> dict:
    resp = (
        supabase.table("blueprints")
        .select("*")
        .eq("blueprint_id", str(blueprint_id))
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise LookupError(f"blueprint {blueprint_id} not found")
    return resp.data[0]


def fetch_deployment_tasks(deployment_id: UUID) -> list[TaskSpec]:
    resp = supabase.table("build_tasks").select("*").eq("deployment_id", str(deployment_id)).execute()
    tasks = []
    for r in resp.data:
        try:
            # We map from DB fields to TaskSpec. 
            # In spec_architect, spec_md contains the JSON representation of the TaskSpec.
            if "spec_md" in r and r["spec_md"]:
                import json
                task_dict = json.loads(r["spec_md"])
                task_dict["task_id"] = UUID(r["task_id"])
                task_dict["status"] = r.get("status", "pending")
                tasks.append(TaskSpec(**task_dict))
        except Exception as e:
            log.error("error_parsing_task_spec", error=str(e), task_id=r.get("task_id"))
    return tasks


def update_task(task: TaskSpec) -> None:
    if task.task_id is None:
        return
    supabase.table("build_tasks").update({
        "status": task.status,
        "issue_number": task.issue_number,
        "branch_name": task.branch_name,
        "pr_number": task.pr_number,
        "pr_url": task.pr_url,
        "ci_status": task.ci_status,
        "notes": task.notes,
    }).eq("task_id", str(task.task_id)).execute()


def update_deployment(deployment_id: UUID, *, status: str, rollback_url: str | None = None) -> None:
    patch: dict = {"status": status}
    if rollback_url is not None:
        patch["release_notes"] = rollback_url   # deployments.release_notes stores the actions URL
    supabase.table("deployments").update(patch).eq("deployment_id", str(deployment_id)).execute()


# ─── GitHub operations ────────────────────────────────────────────────

def _repo():
    return github_client().get_repo(settings.github_build_repo)


def create_issue(task: TaskSpec, blueprint_summary: str) -> int:
    router = ClaudeRouter()
    body_resp = router.complete(
        system=ISSUE_BODY_SYSTEM,
        prompt=ISSUE_BODY_USER.format(
            task_json=task.model_dump_json(),
            blueprint_summary=blueprint_summary[:4000],
        ),
        tier=Tier.DEFAULT,
        max_tokens=900,
    )
    # Why strip both candidates: the previous `or` chain treated whitespace-only
    # LLM output as a non-empty body and created blank GitHub issues. Cursor then
    # had nothing to work from and the task quietly failed.
    body = (body_resp["text"] or "").strip() or (task.rationale or "").strip()
    if not body:
        raise ValueError(
            f"create_issue: both LLM body and task.rationale are empty for slug={task.slug}"
        )
    labels = ["omerion-build", f"phase:{task.phase}"]
    if task.module:
        labels.append(f"module:{task.module}")
    return _create_issue_retryable(task.phase, task.title, body, labels)


@transient_retry(attempts=3, min_wait=2, max_wait=20, exceptions=(GithubException,))
def _create_issue_retryable(phase: str, title: str, body: str, labels: list[str]) -> int:
    """GitHub issue creation retries transient 5xx/429. 422 (validation) is permanent."""
    BUCKETS["github"].acquire()
    issue = _repo().create_issue(title=f"[{phase}] {title}", body=body, labels=labels)
    return issue.number


def create_branch(task: TaskSpec, client_slug: str) -> str:
    repo = _repo()
    convention = settings.agent("build_orchestrator")["branching_convention"]
    branch = convention.format(client_slug=client_slug, task_slug=task.slug)
    default = repo.get_branch(repo.default_branch)
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=default.commit.sha)
    except Exception as exc:  # noqa: BLE001 — branch may already exist; idempotent
        log.info("branch_create_skipped", branch=branch, error=str(exc))
    return branch


def inject_to_cursor(task: TaskSpec, branch: str, blueprint_summary: str) -> None:
    """Write a task brief to the local Cursor/Antigravity inbox directory.

    Cursor (and Antigravity's Claude Code + Gemini panes) are watched for new
    `.task.md` files by a local shim. Output lands as a PR on the pre-created
    branch. If the inbox is unreachable we fall through to a plain commit on
    the branch with a TODO file — unblocks CI so the PR loop still runs.
    """
    inbox = Path(settings.cursor_inbox_dir or "./.cursor_inbox")
    brief = (
        f"# {task.title}\n\n"
        f"**Branch:** `{branch}`\n\n"
        f"**Phase:** {task.phase}\n\n"
        f"## Rationale\n{task.rationale}\n\n"
        f"## Acceptance criteria\n"
        + "\n".join(f"- [ ] {ac}" for ac in task.acceptance_criteria)
        + f"\n\n## Blueprint context\n{blueprint_summary[:2000]}\n"
    )
    # Why explicit OSError handling: silent failures here meant Cursor never
    # got the brief and the task proceeded as if it had — yielding a PR-less
    # branch and a downstream "no PR observed" error with no link to root cause.
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / f"{task.slug}.task.md").write_text(brief)
    except OSError as exc:
        log.warning(
            "cursor_inbox_write_failed",
            inbox=str(inbox),
            task_slug=task.slug,
            error=str(exc),
            error_class=type(exc).__name__,
        )
        raise


def check_pr_once(branch: str) -> dict:
    """Single non-blocking check for a PR on `branch`. Returns current state immediately.

    timeout_reason is None when CI has resolved, "ci_pending" when PR is open but CI
    is still running, and "no_pr_opened" when no PR exists yet.
    """
    repo = _repo()
    prs = list(repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}"))
    if not prs:
        return {"number": None, "url": None, "mergeable": None,
                "ci_status": "pending", "timeout_reason": "no_pr_opened"}
    pr = prs[0]
    ci_status = _pr_ci_status(pr)
    resolved = ci_status in ("success", "failure", "error")
    return {
        "number": pr.number,
        "url": pr.html_url,
        "mergeable": pr.mergeable,
        "ci_status": ci_status,
        "timeout_reason": None if resolved else "ci_pending",
    }


def poll_pr(branch: str, timeout_seconds: int = 300) -> dict | None:
    """Poll for a PR on `branch` until CI resolves or the deadline is reached.

    Uses short sleep intervals and a conservative 5-minute default timeout
    (down from 1 hour) so the LangGraph executor thread is not blocked for
    extended periods. Callers that need longer waits should use check_pr_once()
    in a retry loop at the graph level.

    Returns:
      - dict with timeout_reason=None when CI has resolved (success/failure/error).
      - dict with timeout_reason="ci_pending" when PR opened but CI didn't finish.
      - dict with timeout_reason="no_pr_opened" when no PR appeared before deadline.
      - None only when the GitHub client itself is unusable.
    """
    repo = _repo()
    poll_interval = int(settings.agent("build_orchestrator")["ci_poll_interval_seconds"])
    deadline = time.time() + timeout_seconds
    last_pr = None

    while time.time() < deadline:
        prs = list(repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}"))
        if prs:
            pr = prs[0]
            last_pr = pr
            ci_status = _pr_ci_status(pr)
            if ci_status in ("success", "failure", "error"):
                return {
                    "number": pr.number,
                    "url": pr.html_url,
                    "mergeable": pr.mergeable,
                    "ci_status": ci_status,
                    "timeout_reason": None,
                }
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval + random.uniform(0, 5), remaining))

    if last_pr is not None:
        log.warning(
            "build_poll_pr_ci_timeout",
            branch=branch,
            timeout_seconds=timeout_seconds,
            pr_number=last_pr.number,
            hint="Increase timeout_seconds or use check_pr_once() in a retry loop",
        )
        return {
            "number": last_pr.number,
            "url": last_pr.html_url,
            "mergeable": last_pr.mergeable,
            "ci_status": "pending",
            "timeout_reason": "ci_pending",
        }
    log.warning(
        "build_poll_pr_no_pr_opened",
        branch=branch,
        timeout_seconds=timeout_seconds,
        hint="Cursor may not have produced a PR; check .cursor_inbox delivery",
    )
    return {
        "number": None,
        "url": None,
        "mergeable": None,
        "ci_status": "pending",
        "timeout_reason": "no_pr_opened",
    }


def _pr_ci_status(pr) -> str:
    commit = list(pr.get_commits())[-1]
    checks = list(commit.get_check_runs())
    if not checks:
        status = commit.get_combined_status()
        return status.state  # pending | success | failure | error
    if any(c.conclusion == "failure" for c in checks):
        return "failure"
    if all(c.status == "completed" and c.conclusion == "success" for c in checks):
        return "success"
    return "pending"


def merge_pr(pr_number: int, repo_full: str | None = None) -> bool:
    """Squash-merge — blocked until VALIDATOR has posted an APPROVED review.

    `repo_full` ("owner/repo") is required when called from outside the graph
    (e.g., the webhook path). When called from the graph it falls back to
    settings.github_build_repo which _repo() already uses.
    """
    _full = repo_full or settings.github_build_repo

    # Check validator approval via Supabase — avoids cross-agent import coupling.
    # build_tasks.ci_status is the shared contract; validator writes "validator_approved"
    # after posting its GitHub review. This query is the authoritative check.
    task_row = (
        supabase.table("build_tasks")
        .select("ci_status")
        .eq("pr_number", pr_number)
        .maybe_single()
        .execute()
    )
    if not task_row.data or task_row.data.get("ci_status") != "validator_approved":
        log.warning("merge_blocked_awaiting_validator", pr=pr_number, repo=_full)
        return False

    repo = _repo()
    pr = repo.get_pull(pr_number)
    if not pr.mergeable:
        return False
    pr.merge(merge_method="squash")
    return True


# ─── JSON parsing helper ──────────────────────────────────────────────

# Why greedy + bracket counting instead of `\[.*?\]`: the non-greedy variant
# captured the first `[` paired with the next `]` it could find, which fired
# on stray brackets in the LLM's prose (e.g. "see [1]"). Now we find the first
# `[`, walk forward counting depth, and only parse if we close cleanly.

def _extract_json_array(raw: str) -> list:
    if not raw:
        return []
    start = raw.find("[")
    if start < 0:
        return []
    depth = 0
    end = -1
    for i in range(start, len(raw)):
        c = raw[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return []
    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


# ─── Client-mode Google Docs deliverables ─────────────────────────────

def _google_services():
    """Return (drive, docs) services via personal-OAuth google_client."""
    from omerion_core.clients.google_client import drive_service, docs_service
    return drive_service(), docs_service()


def ensure_client_drive_folder(client_slug: str, existing_id: str | None = None) -> str:
    drive, _ = _google_services()
    if existing_id:
        return existing_id
    cfg = settings.agent("build_orchestrator")["client_deliverables"]
    root_env = cfg.get("root_drive_folder_env", "GOOGLE_CLIENT_DELIVERABLES_FOLDER_ID")
    root_folder_id = getattr(settings, root_env.lower(), None) or ""
    meta = {
        "name": cfg.get("per_client_subfolder_template", "{client_slug}").format(client_slug=client_slug),
        "mimeType": "application/vnd.google-apps.folder",
    }
    if root_folder_id:
        meta["parents"] = [root_folder_id]
    folder = drive.files().create(body=meta, fields="id").execute()
    return folder["id"]


def author_client_doc(
    router: ClaudeRouter,
    doc_type: str,
    client_slug: str,
    blueprint: dict,
    deployment_summary: str,
) -> str:
    proposal = blueprint.get("proposal") or {}
    resp = router.complete(
        system=CLIENT_DOC_SYSTEM,
        prompt=CLIENT_DOC_USER.format(
            doc_type=doc_type,
            client_slug=client_slug,
            persona=blueprint.get("persona") or "unknown",
            service_package=proposal.get("recommended_service_package") or "—",
            demo_reference=proposal.get("demo_reference") or "—",
            blueprint_json=json.dumps(blueprint, default=str)[:10000],
            deployment_summary=deployment_summary[:4000],
        ),
        tier=Tier.HEAVY,
        max_tokens=3000,
    )
    return resp["text"] or ""


def create_client_doc(
    folder_id: str,
    client_slug: str,
    doc_type: str,
    markdown_body: str,
) -> ClientDeliverable:
    drive, docs = _google_services()
    title = f"Omerion — {client_slug} — {doc_type}"
    try:
        created = drive.files().create(
            body={
                "name": title,
                "mimeType": "application/vnd.google-apps.document",
                "parents": [folder_id],
            },
            fields="id",
        ).execute()
        doc_id = created["id"]
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{
                "insertText": {"location": {"index": 1}, "text": markdown_body},
            }]},
        ).execute()
        return ClientDeliverable(
            doc_type=doc_type,  # type: ignore[arg-type]
            doc_id=doc_id,
            doc_url=f"https://docs.google.com/document/d/{doc_id}/edit",
            status="created",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("client_doc_create_failed", doc_type=doc_type, error=str(exc))
        return ClientDeliverable(doc_type=doc_type, status="failed")  # type: ignore[arg-type]


def upsert_client_record(
    client_id: UUID | None,
    client_slug: str,
    drive_folder_id: str,
    persona: str | None,
    service_package: str | None,
    account_id: UUID | None,
    opportunity_id: UUID | None,
) -> UUID:
    payload = {
        "slug": client_slug,
        "drive_folder_id": drive_folder_id,
        "persona": persona,
        "service_package": service_package,
        "account_id": str(account_id) if account_id else None,
        "opportunity_id": str(opportunity_id) if opportunity_id else None,
        "status": "active",
    }
    if client_id:
        supabase.table("clients").update(payload).eq("client_id", str(client_id)).execute()
        return client_id
    resp = supabase.table("clients").insert(payload).execute()
    return UUID(resp.data[0]["client_id"])
