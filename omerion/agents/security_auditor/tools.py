"""Deterministic security scan tools for SECURITY_AUDITOR.

ALL finding detection is deterministic. The LLM is used ONLY in graph.py
to write a weekly executive brief — it never decides whether a finding is real.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

from .state import SecurityFinding

log = get_logger("omerion.agents.security_auditor")

_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("OpenAI key",     re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Anthropic key",  re.compile(r"ant-[A-Za-z0-9\-]{30,}")),
    ("Pinecone key",   re.compile(r"pcsk_[A-Za-z0-9]{30,}")),
    ("GitHub PAT",     re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("JWT/Supabase",   re.compile(r"eyJ[A-Za-z0-9+/=]{40,}")),
]


def scan_env_for_secrets(repo_root: str) -> list[SecurityFinding]:
    """Scan .env files for raw secret patterns.

    Deterministic: pure regex matching. No LLM.
    Skips .env.example files (intentionally contain placeholder patterns).
    """
    findings: list[SecurityFinding] = []
    env_files = list(Path(repo_root).rglob(".env*"))

    for env_file in env_files:
        if ".env.example" in str(env_file) or "__pycache__" in str(env_file):
            continue
        try:
            text = env_file.read_text(errors="ignore")
        except Exception:
            continue
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(SecurityFinding(
                    finding_type="secret",
                    severity="critical",
                    resource=str(env_file.relative_to(repo_root)),
                    description=f"Potential {label} found in {env_file.name}",
                    remediation="Remove the secret from the file. Use environment variables or a vault. Rotate the key immediately.",
                ))
                break  # one finding per file
    return findings


def scan_dependencies_for_cves(repo_root: str) -> list[SecurityFinding]:
    """Run pip-audit against requirements.txt and return CVE findings.

    Deterministic: subprocess + JSON parse of pip-audit output.
    """
    findings: list[SecurityFinding] = []
    try:
        result = subprocess.run(
            ["pip-audit", "--format=json", "--requirement", "requirements.txt"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if not result.stdout:
            return findings
        data = json.loads(result.stdout)
        for vuln in data.get("vulnerabilities", []):
            severity = "high" if vuln.get("fix_versions") else "medium"
            findings.append(SecurityFinding(
                finding_type="dependency_cve",
                severity=severity,
                resource=f"{vuln.get('name')}=={vuln.get('version')}",
                description=f"CVE {vuln.get('id')}: {vuln.get('description', '')[:200]}",
                cve_id=vuln.get("id"),
                remediation=(
                    f"Upgrade to: {vuln.get('fix_versions', ['unknown'])[0]}"
                    if vuln.get("fix_versions") else "no fix available"
                ),
            ))
    except subprocess.TimeoutExpired:
        log.warning("security_auditor_pip_audit_timeout")
    except FileNotFoundError:
        log.info("security_auditor_pip_audit_not_installed")
    except Exception as exc:
        log.warning("security_auditor_pip_audit_error", error=str(exc))
    return findings


def scan_exposed_endpoints(repo_root: str) -> list[SecurityFinding]:
    """Detect route files that define HTTP endpoints without auth dependencies.

    Deterministic: grep for route patterns against auth pattern absence.
    Heuristic — flags for human review, does not block.
    """
    findings: list[SecurityFinding] = []
    _ROUTE_RE = re.compile(r'@\w+\.(get|post|put|delete|patch)\(')
    _AUTH_RE = re.compile(r'(Depends|require_auth|get_current_user|verify_token)', re.IGNORECASE)

    for fpath in Path(repo_root).rglob("*.py"):
        if "__pycache__" in str(fpath) or "test" in str(fpath):
            continue
        try:
            text = fpath.read_text(errors="ignore")
        except Exception:
            continue
        if _ROUTE_RE.search(text) and not _AUTH_RE.search(text):
            rel = str(fpath.relative_to(repo_root))
            findings.append(SecurityFinding(
                finding_type="exposed_endpoint",
                severity="medium",
                resource=rel,
                description=f"Route file {rel} defines HTTP routes but has no auth dependency detected.",
                remediation="Add `Depends(get_current_user)` or equivalent auth check to all route handlers.",
            ))
    return findings


def persist_findings(run_id: str, findings: list[SecurityFinding]) -> int:
    """Write security findings to security_findings table."""
    if not findings:
        return 0
    try:
        rows = [
            {
                "run_id": run_id,
                "finding_type": f.finding_type,
                "severity": f.severity,
                "resource": f.resource,
                "description": f.description,
                "cve_id": f.cve_id,
                "remediation": f.remediation,
            }
            for f in findings
        ]
        supabase.table("security_findings").insert(rows).execute()
        return len(rows)
    except Exception as exc:
        log.warning("security_persist_failed", error=str(exc))
        return 0
