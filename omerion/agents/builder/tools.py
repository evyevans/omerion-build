"""Tools for BUILDER (Agent #11).

Talks to: GitHub Contents API (read file tree, commit changes, open PRs),
Supabase (build_tasks table), subprocess sandbox (pytest), Claude router (code gen).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from github import GithubException

from omerion_core.clients.github_client import github_client
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.rate_limit.token_bucket import BUCKETS
from omerion_core.retry import transient_retry
from omerion_core.settings import settings

from .prompts import BUILDER_SYSTEM, CODE_GEN_USER
from .state import TaskResult

log = get_logger("omerion.agents.builder")


# ─── JSON extraction ──────────────────────────────────────────────────────────

def extract_json_file_changes(raw: str) -> list[dict]:
    """Extract a JSON array from LLM output that may contain prose."""
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group())
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and "path" in d and "content" in d]
    except json.JSONDecodeError:
        pass
    return []


# ─── Guardrail checks ─────────────────────────────────────────────────────────

def check_secret_patterns(changes: list[dict], patterns: list[str]) -> bool:
    """Return True if any generated file contains a secret pattern."""
    for change in changes:
        content = change.get("content", "")
        for pat in patterns:
            if pat in content:
                return True
    return False


def check_banned_paths(changes: list[dict], banned_prefixes: list[str]) -> bool:
    """Return True if any generated file targets a banned path prefix."""
    for change in changes:
        path = change.get("path", "")
        for prefix in banned_prefixes:
            if path.startswith(prefix):
                return True
    return False


# ─── Prompt helpers ───────────────────────────────────────────────────────────

def build_acceptance_criteria_md(criteria: list[str]) -> str:
    if not criteria:
        return "- [ ] Feature verified end-to-end."
    return "\n".join(f"- [ ] {c}" for c in criteria)


# ─── GitHub helpers ───────────────────────────────────────────────────────────

_cached_repo: Any = None


def _repo():
    global _cached_repo
    repo_name = settings.github_build_repo
    if not repo_name:
        raise RuntimeError("GITHUB_BUILD_REPO setting is required for BUILDER")
    if _cached_repo is None:
        _cached_repo = github_client().get_repo(repo_name)
    return _cached_repo


def get_file_tree(branch: str, max_files: int = 40) -> list[str]:
    """Return a list of file paths on the branch (truncated to max_files)."""
    BUCKETS["github"].acquire()
    repo = _repo()
    try:
        contents = repo.get_git_tree(branch, recursive=True)
        return [f.path for f in contents.tree if f.type == "blob"][:max_files]
    except GithubException as exc:
        log.warning("builder.file_tree_failed", branch=branch, error=str(exc))
        return []


def get_file_contents(paths: list[str], branch: str, max_chars_each: int = 8000) -> dict[str, str]:
    """Fetch file content for each path from GitHub. Returns {path: content}."""
    repo = _repo()
    result: dict[str, str] = {}
    for path in paths[:12]:  # cap at 12 files to avoid context overflow
        BUCKETS["github"].acquire()
        try:
            f = repo.get_contents(path, ref=branch)
            if hasattr(f, "decoded_content"):
                result[path] = f.decoded_content.decode("utf-8", errors="replace")[:max_chars_each]
        except (GithubException, AttributeError) as exc:
            log.warning("builder.file_read_failed", path=path, error=str(exc))
    return result


def _is_retryable_github_exc(exc: BaseException) -> bool:
    """GithubException with 5xx / 502 / 503 / 429 / abuse-rate-limit status is retryable.

    Anything else (404, 422, auth failures) is permanent and must not be retried.
    """
    if not isinstance(exc, GithubException):
        return False
    status = getattr(exc, "status", 0)
    return status in (408, 429, 500, 502, 503, 504)


@transient_retry(attempts=3, min_wait=2, max_wait=20,
                 exceptions=(GithubException,))
def _commit_single_file(repo, change: dict, branch: str, message: str) -> str:
    """Returns the commit SHA created for this file."""
    from github import UnknownObjectException
    path: str = change["path"]
    content: str = change["content"]
    BUCKETS["github"].acquire()
    try:
        existing = repo.get_contents(path, ref=branch)
        result = repo.update_file(path, message, content, existing.sha, branch=branch)
        log.info("builder.file_updated", path=path, branch=branch)
    except UnknownObjectException:
        result = repo.create_file(path, message, content, branch=branch)
        log.info("builder.file_created", path=path, branch=branch)
    except GithubException as exc:
        if _is_retryable_github_exc(exc):
            log.warning("builder.commit_retryable", path=path,
                        status=getattr(exc, "status", 0), error=str(exc))
        raise
    return result["commit"].sha


def commit_changes(changes: list[dict], branch: str, message: str) -> str | None:
    """Commit a list of {path, content} changes to a branch via GitHub Contents API.

    Returns the SHA of the last file's commit — a pointer stored in state
    instead of the full file content to avoid checkpoint bloat.
    """
    repo = _repo()
    last_sha: str | None = None
    for change in changes:
        last_sha = _commit_single_file(repo, change, branch, message)
    return last_sha


@transient_retry(attempts=3, min_wait=2, max_wait=20,
                 exceptions=(GithubException,))
def open_pr(task: TaskResult, pr_body: str, base_branch: str = "main") -> tuple[int, str]:
    """Open a PR for the task branch. Returns (pr_number, pr_url).

    Retries transient GitHub failures (5xx/429). 422 "PR already exists" is
    permanent and surfaces to the caller, which should call pr_already_exists()
    before this to short-circuit.
    """
    BUCKETS["github"].acquire()
    repo = _repo()
    pr = repo.create_pull(
        title=f"[BUILDER] {task.title}",
        body=pr_body,
        head=task.branch_name,
        base=base_branch,
    )
    return pr.number, pr.html_url


def pr_already_exists(branch_name: str) -> tuple[bool, int | None, str | None]:
    """Check if a PR is already open for this branch."""
    BUCKETS["github"].acquire()
    repo = _repo()
    prs = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch_name}")
    for pr in prs:
        return True, pr.number, pr.html_url
    return False, None, None


# ─── Sandbox test runner ──────────────────────────────────────────────────────

def run_tests_in_sandbox(
    changes: list[dict],
    branch: str,
    test_command: str = "uv run pytest",
    timeout: int = 120,
    isolation_key: str = "",
) -> tuple[bool, str]:
    """
    Clone the repo branch into a tempdir, apply changes, run tests.
    Returns (passed: bool, output: str).
    Falls back to (False, "git not available") if git binary absent.
    """
    if not shutil.which("git"):
        return False, "git binary not available in container — use CI fallback"

    repo_name = settings.github_build_repo
    token = settings.github_token
    clone_url = f"https://x-access-token:{token}@github.com/{repo_name}.git"

    safe_key = isolation_key.replace("/", "-")[:40] if isolation_key else ""
    prefix = f"omerion-builder-{safe_key}-" if safe_key else "omerion-builder-"
    with tempfile.TemporaryDirectory(prefix=prefix) as tmpdir:
        try:
            subprocess.run(
                ["git", "clone", "--depth=1", "--branch", branch, clone_url, tmpdir],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            return False, f"git clone failed: {exc.stderr.decode()[:500]}"
        except subprocess.TimeoutExpired:
            return False, "git clone timed out"

        # Apply generated file changes
        for change in changes:
            file_path = Path(tmpdir) / change["path"]
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(change["content"])

        # Run tests
        cmd = test_command.split()
        try:
            result = subprocess.run(
                cmd,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout + result.stderr)[-3000:]  # last 3k chars
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, f"Tests timed out after {timeout}s"
        except FileNotFoundError:
            return False, f"Test command not found: {cmd[0]}"


# ─── Code generation ──────────────────────────────────────────────────────────

def generate_code_changes(
    router: ClaudeRouter,
    task: TaskResult,
    workflow_spec_md: str,
    file_tree: list[str],
    file_contents: dict[str, str],
    error_context: str = "",
) -> tuple[list[dict], dict]:
    """Ask Claude Opus to generate file changes for the task. Returns ([{path, content}], resp)."""
    file_tree_md = "\n".join(f"- {p}" for p in file_tree) or "(empty branch)"
    file_contents_md = "\n\n".join(
        f"### {path}\n```python\n{content}\n```"
        for path, content in file_contents.items()
    ) or "(no files fetched)"
    error_section = (
        f"\n## Previous test failure\n```\n{error_context}\n```\nFix the above error."
        if error_context else ""
    )

    resp = router.complete(
        system=BUILDER_SYSTEM,
        prompt=CODE_GEN_USER.format(
            title=task.title,
            spec_md=task.spec_md,
            workflow_spec_md=workflow_spec_md,
            branch_name=task.branch_name,
            file_tree_md=file_tree_md,
            file_contents_md=file_contents_md[:10000],
            error_context=error_section,
        ),
        tier=Tier.HEAVY,
        max_tokens=4096,
    )
    raw = resp.get("text") or "[]"
    return extract_json_file_changes(raw), resp


def author_pr_body(router: Any, task: TaskResult, changes: list[dict], test_command: str) -> str:
    """Build a PR body deterministically — no LLM call needed."""
    file_list = "\n".join(f"- `{c['path']}`" for c in changes) or "_(no files)_"
    criteria_md = build_acceptance_criteria_md(task.acceptance_criteria)
    attempt_note = (
        f"Completed on attempt {task.attempts} of 3."
        if task.attempts > 1
        else "Completed on first attempt."
    )
    return (
        f"## {task.title}\n\n"
        f"{task.rationale or '_(no rationale)_'}\n\n"
        f"## Acceptance Criteria\n\n{criteria_md}\n\n"
        f"## Files Changed\n\n{file_list}\n\n"
        f"## Testing\n\nRan `{test_command}`. {attempt_note}\n\n"
        f"---\n_Generated by BUILDER (Agent #11)_"
    )


# ─── Supabase task operations ─────────────────────────────────────────────────

def load_full_tasks(deployment_id: UUID, slugs: list[str]) -> list[dict]:
    """Load full TaskSpec records from build_tasks by deployment_id and slugs."""
    resp = (
        supabase.table("build_tasks")
        .select("task_id, slug, title, acceptance_criteria, rationale, branch_name, status, spec_md")
        .eq("deployment_id", str(deployment_id))
        .in_("slug", slugs)
        .execute()
    )
    return resp.data or []


def update_task_status(
    task_id: UUID,
    status: str,
    notes: str = "",
    pr_number: int | None = None,
    pr_url: str | None = None,
) -> None:
    patch: dict[str, Any] = {"status": status, "notes": notes}
    if pr_number is not None:
        patch["pr_number"] = pr_number
    if pr_url is not None:
        patch["pr_url"] = pr_url
    supabase.table("build_tasks").update(patch).eq("task_id", str(task_id)).execute()
