"""Deterministic tools for QA_TESTER.

Design principle: every check here is a pure function with no LLM.
The LLM is invoked in graph.py ONLY when tests fail and root-cause
narrative is needed — it never decides whether the build passes.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any
from uuid import UUID

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.agents.qa_tester")

_PYTEST_SUMMARY_RE = re.compile(
    r"(?:(\d+) passed)?(?:,\s*)?(?:(\d+) failed)?(?:,\s*)?(?:(\d+) error(?:s)?)?",
    re.IGNORECASE,
)
_COVERAGE_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+)%")


def parse_pytest_output(raw: str) -> dict[str, int]:
    """Parse pytest stdout into pass/fail/error counts. Deterministic: pure regex."""
    passed = failed = errors = 0
    for line in raw.splitlines():
        m = _PYTEST_SUMMARY_RE.search(line)
        if m and any(m.groups()):
            passed = int(m.group(1) or 0)
            failed = int(m.group(2) or 0)
            errors = int(m.group(3) or 0)
    return {
        "total": passed + failed + errors,
        "passed": passed,
        "failed": failed,
        "errors": errors,
    }


def parse_coverage_pct(raw: str) -> float:
    """Extract overall coverage % from pytest-cov output. Returns 0.0 if not found."""
    m = _COVERAGE_RE.search(raw)
    if m:
        return int(m.group(1)) / 100.0
    return 0.0


def coverage_meets_threshold(coverage_pct: float, threshold: float) -> bool:
    """Deterministic coverage gate. No LLM."""
    return coverage_pct >= threshold


def run_test_suite(test_command: str, repo_root: str, timeout_sec: int = 120) -> dict[str, Any]:
    """Execute the test suite as a subprocess. NEVER raises — failures captured in return dict."""
    try:
        result = subprocess.run(
            test_command.split(),
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        stdout = result.stdout + result.stderr
        return {
            "raw_output": stdout,
            "exit_code": result.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        log.warning("qa_tester_test_suite_timed_out", timeout=timeout_sec)
        return {"raw_output": f"TIMEOUT after {timeout_sec}s", "exit_code": -1, "timed_out": True}
    except Exception as exc:
        log.error("qa_tester_test_suite_error", error=str(exc))
        return {"raw_output": str(exc), "exit_code": -1, "timed_out": False}


def fetch_build_task(build_task_id: UUID) -> dict[str, Any] | None:
    """Load build task context from Supabase by primary key."""
    try:
        resp = (
            supabase.table("build_tasks")
            .select("task_id, spec_md, acceptance_criteria, test_command, repo_path")
            .eq("task_id", str(build_task_id))
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:
        log.warning("qa_tester_fetch_build_task_failed", error=str(exc))
        return None


def fetch_build_task_by_slug(deployment_id: UUID, task_slug: str) -> dict[str, Any] | None:
    """Load build task context by composite key (deployment_id + slug).

    Used when the BUILD_TASK_COMPLETED event payload lacks task_id — the builder
    only emits deployment_id + task_slug, so we resolve the full row here.
    """
    try:
        resp = (
            supabase.table("build_tasks")
            .select("task_id, spec_md, acceptance_criteria, test_command, repo_path")
            .eq("deployment_id", str(deployment_id))
            .eq("slug", task_slug)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:
        log.warning("qa_tester_fetch_build_task_by_slug_failed", error=str(exc))
        return None


def persist_test_result(
    run_id: str,
    build_task_id: UUID | None,
    status: str,
    tests_total: int,
    tests_passed: int,
    tests_failed: int,
    coverage_pct: float,
    failure_summary: str,
    raw_output: str,
) -> str | None:
    """Write QA result to qa_test_results table. Returns inserted row id."""
    try:
        resp = supabase.table("qa_test_results").insert({
            "run_id": run_id,
            "build_task_id": str(build_task_id) if build_task_id else None,
            "status": status,
            "tests_total": tests_total,
            "tests_passed": tests_passed,
            "tests_failed": tests_failed,
            "coverage_pct": round(coverage_pct * 100, 2),
            "failure_summary": failure_summary[:4000] if failure_summary else None,
            "raw_output": raw_output[:8000] if raw_output else None,
        }).execute()
        return resp.data[0]["id"] if resp.data else None
    except Exception as exc:
        log.warning("qa_tester_persist_failed", error=str(exc))
        return None
