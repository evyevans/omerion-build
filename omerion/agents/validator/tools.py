"""Tools for VALIDATOR — GitHub diff fetch, lint, review post, merge gate."""
from __future__ import annotations

import re
from uuid import UUID

from github import Github, GithubException

from omerion_core.clients.github_client import github_client
from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.agents.validator")

# ─── Deterministic lint rules ─────────────────────────────────────────────────
# Match only added lines (prefix '+') to avoid false positives on deleted code.
_CONSOLE_LOG_RE = re.compile(r"^\+[^\n]*console\.log\(", re.MULTILINE)
_BARE_PRINT_RE  = re.compile(r"^\+\s*print\(", re.MULTILINE)


def lint_diff(patch: str, changed_files: list[str]) -> list[str]:
    """Return list of lint error strings. Empty list = clean."""
    errors: list[str] = []

    if _CONSOLE_LOG_RE.search(patch):
        errors.append("LINT: console.log() found — remove all debugging statements before merge.")

    if _BARE_PRINT_RE.search(patch):
        errors.append("LINT: bare print() found — use the logger (omerion_core.logging.get_logger) instead.")

    has_test = any("test" in f.lower() or "spec" in f.lower() for f in changed_files)
    if not has_test:
        errors.append("LINT: No test file detected in this PR. Every change must be accompanied by tests.")

    return errors


_FILE_HEADER_RE = re.compile(r"(?=^--- a/)", re.MULTILINE)
_MAX_FILES_PER_REVIEW = 10


def chunk_diff_by_file(patch: str) -> list[str]:
    """Split a unified diff into per-file chunks, capped at _MAX_FILES_PER_REVIEW."""
    if not patch:
        return []
    parts = _FILE_HEADER_RE.split(patch)
    chunks = [p.strip() for p in parts if p.strip()]
    return chunks[:_MAX_FILES_PER_REVIEW]


def fetch_task_spec_for_branch(head_branch: str) -> dict | None:
    """Query build_tasks for the row whose branch matches head_branch."""
    result = (
        supabase.table("build_tasks")
        .select("task_id, acceptance_criteria, spec_md, branch")
        .eq("branch", head_branch)
        .limit(1)
        .execute()
    )
    rows = result.data
    if not rows:
        return None
    return rows[0]


def fetch_pr_diff(repo_full: str, pr_number: int) -> tuple[str, list[str]]:
    """Return (unified_diff_patch, list_of_changed_file_paths) for a PR."""
    gh: Github = github_client()
    repo = gh.get_repo(repo_full)
    pr = repo.get_pull(pr_number)

    changed_files: list[str] = []
    patch_parts: list[str] = []

    for f in pr.get_files():
        changed_files.append(f.filename)
        if f.patch:
            patch_parts.append(f"--- a/{f.filename}\n+++ b/{f.filename}\n{f.patch}")

    return "\n".join(patch_parts), changed_files


def post_github_review(
    repo_full: str,
    pr_number: int,
    verdict: str,
    review_body: str,
    line_comments: list[dict],
) -> None:
    """Post APPROVE or REQUEST_CHANGES review on the PR via GitHub API."""
    gh: Github = github_client()
    repo = gh.get_repo(repo_full)
    pr = repo.get_pull(pr_number)

    event = "APPROVE" if verdict == "approve" else "REQUEST_CHANGES"
    comments = [{"path": c["path"], "line": c["line"], "body": c["body"]} for c in line_comments]

    try:
        pr.create_review(body=review_body, event=event, comments=comments)
    except GithubException as exc:
        if exc.status == 422 and comments:
            # LLM-generated line numbers may not correspond to actual diff positions.
            # Retry without inline comments — the review body still delivers the verdict.
            log.warning(
                "val_line_comments_rejected_retrying",
                pr=pr_number,
                error=str(exc.data),
            )
            pr.create_review(body=review_body, event=event, comments=[])
        else:
            raise
    log.info("validator_review_posted", pr=pr_number, verdict=verdict)


def update_task_ci_status(task_id: UUID, status: str) -> None:
    """Write validator outcome into build_tasks.ci_status."""
    supabase.table("build_tasks").update({"ci_status": status}).eq("task_id", str(task_id)).execute()


_MAX_AUTO_REJECTIONS = 3


def get_pr_rejection_count(task_id: UUID) -> int:
    """Return the current rejection_count for a build_task."""
    result = (
        supabase.table("build_tasks")
        .select("rejection_count")
        .eq("task_id", str(task_id))
        .limit(1)
        .execute()
    )
    if not result.data:
        return 0
    return result.data[0].get("rejection_count", 0)


def increment_pr_rejection_count(task_id: UUID) -> int:
    """Atomically increment rejection_count and return the new value.

    Uses a single UPDATE...RETURNING to avoid the read-modify-write race:
    if two validator runs hit the same task_id concurrently, both reads
    would return the same count and both writes would set the same value,
    losing one increment. The atomic form lets Postgres handle the lock.
    """
    result = supabase.rpc(
        "increment_task_rejection_count",
        {"p_task_id": str(task_id)},
    ).execute()
    # RPC returns the new count directly. Fall back to a plain read if the
    # RPC hasn't been deployed yet (graceful degradation during migration).
    if result.data is not None:
        return int(result.data)
    # Fallback: non-atomic (acceptable only before RPC migration is applied)
    current = get_pr_rejection_count(task_id)
    new_count = current + 1
    supabase.table("build_tasks").update(
        {"rejection_count": new_count}
    ).eq("task_id", str(task_id)).execute()
    return new_count


def rejection_limit_exceeded(task_id: UUID) -> bool:
    return get_pr_rejection_count(task_id) >= _MAX_AUTO_REJECTIONS


def validator_approval_exists(repo_full: str, pr_number: int) -> bool:
    """Return True if the authenticated bot has posted an APPROVED review on this PR.

    Called by build_orchestrator.merge_pr() to gate merges.
    """
    gh: Github = github_client()
    repo = gh.get_repo(repo_full)
    pr = repo.get_pull(pr_number)
    bot_login = gh.get_user().login

    for review in pr.get_reviews():
        if review.state == "APPROVED" and review.user.login == bot_login:
            return True
    return False
