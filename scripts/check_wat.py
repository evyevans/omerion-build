#!/usr/bin/env python3
"""WAT compliance checker (Fix #11).

Walks `departments/<dept>/` and asserts each agent has a matching
`<agent>.skill.md` whose frontmatter parses and whose prose contains the
required sections. Tuned to the **current flat layout** (skill.md alongside
agent.py) rather than the per-agent directory layout that Fix #7's
PROMPT_AGENT_MIGRATION_GUIDE.md describes as the eventual target — the
checker will be tightened once that migration completes.

Today's contract per agent:

  REQUIRED:
    - departments/<dept>/<agent>.py exists with a `class X(...)` declaring
      `name = "<agent>"`.
    - departments/<dept>/<agent>.skill.md exists.
    - skill.md frontmatter has: name, department, hitl, model_tier.
    - skill.md frontmatter `name` matches the agent's class-level `name`.
    - skill.md body contains all required H2 sections:
        Identity & Scope, Trigger & Input Contract, Reasoning Chain,
        Output Contract, Tool Inventory, HITL Behavior,
        Confidence Source, Success Criteria, Failure Modes & Recovery,
        Guardrails

  RECOMMENDED (warn, not fail):
    - sibling <agent>_prompts.py for any agent whose model_tier != "NONE"
      that defines SYSTEM_PROMPT, GUARDRAILS, SUCCESS_EXEMPLAR.

Exit codes:
  0 = all required checks pass
  1 = at least one agent failed
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]

REQUIRED_FRONTMATTER = {"name", "department", "hitl", "model_tier"}
REQUIRED_SECTIONS = [
    "Identity & Scope",
    "Trigger & Input Contract",
    "Reasoning Chain",
    "Output Contract",
    "Tool Inventory",
    "HITL Behavior",
    "Confidence Source",
    "Success Criteria",
    "Failure Modes & Recovery",
    "Guardrails",
]

# Files under departments/ that aren't agents and should be skipped.
SKIP_FILES = {"__init__.py", "rsi_workflow.py"}

# Skill files that document a department or workflow, not a single agent.
DEPARTMENT_SKILL_FILES = {"rsi.skill.md"}


@dataclass
class AgentReport:
    path: Path
    skill_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _parse_frontmatter(text: str) -> tuple[dict | None, str]:
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None, parts[2]
    return fm, parts[2]


def _agent_name_from_py(path: Path) -> str | None:
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^\s{0,4}name\s*=\s*["\']([a-z0-9_]+)["\']', src, re.M)
    return m.group(1) if m else None


def _audit_agent(py_path: Path) -> AgentReport:
    rep = AgentReport(path=py_path)
    agent_name = _agent_name_from_py(py_path)
    if not agent_name:
        rep.warnings.append(
            "no class-level `name = \"...\"` found; treating filename as agent stem"
        )
        agent_name = py_path.stem

    skill_path = py_path.with_suffix(".skill.md")
    if not skill_path.exists():
        rep.errors.append(f"missing skill.md: expected {skill_path.relative_to(REPO)}")
        return rep
    rep.skill_path = skill_path

    raw = skill_path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(raw)
    if fm is None:
        rep.errors.append("skill.md missing or unparseable YAML frontmatter")
        return rep

    missing_fm = REQUIRED_FRONTMATTER - set(fm.keys())
    if missing_fm:
        rep.errors.append(f"frontmatter missing keys: {sorted(missing_fm)}")

    if fm.get("name") and fm["name"] != agent_name:
        rep.errors.append(
            f"frontmatter name={fm['name']!r} != class name={agent_name!r}"
        )

    for section in REQUIRED_SECTIONS:
        # Allow `## Section` or `## Section (note)`
        pattern = rf"^##\s+{re.escape(section)}(\s|\(|$)"
        if not re.search(pattern, body, re.M):
            rep.errors.append(f"missing required H2 section: {section!r}")

    # RECOMMENDED prompts.py check
    tier = (fm.get("model_tier") or "").upper()
    if tier and tier != "NONE":
        prompts_path = py_path.with_name(f"{py_path.stem}_prompts.py")
        if not prompts_path.exists():
            rep.warnings.append(
                f"recommended sibling not found: {prompts_path.name} "
                "(Fix #7 migration target)"
            )
        else:
            psrc = prompts_path.read_text(encoding="utf-8")
            for token in ("SYSTEM_PROMPT", "GUARDRAILS", "SUCCESS_EXEMPLAR"):
                if token not in psrc:
                    rep.warnings.append(f"{prompts_path.name} missing {token}")
    return rep


def main() -> int:
    dept_root = REPO / "departments"
    reports: list[AgentReport] = []
    for dept_dir in sorted(dept_root.iterdir()):
        if not dept_dir.is_dir():
            continue
        for py in sorted(dept_dir.glob("*.py")):
            if py.name in SKIP_FILES or py.name.endswith("_prompts.py") \
                    or py.name.endswith("_tools.py"):
                continue
            reports.append(_audit_agent(py))

    failed = [r for r in reports if r.errors]
    print(f"WAT check: {len(reports)} agents inspected, {len(failed)} with errors\n")
    for r in reports:
        if not (r.errors or r.warnings):
            continue
        rel = r.path.relative_to(REPO)
        print(f"  {rel}")
        for e in r.errors:
            print(f"    ERROR   {e}")
        for w in r.warnings:
            print(f"    warn    {w}")
        print()

    # Cross-check: any *.skill.md that doesn't match a .py?
    orphans = []
    for dept_dir in sorted(dept_root.iterdir()):
        if not dept_dir.is_dir():
            continue
        for skill in sorted(dept_dir.glob("*.skill.md")):
            if skill.name in DEPARTMENT_SKILL_FILES:
                continue
            py = skill.with_suffix("").with_suffix(".py")
            if not py.exists():
                orphans.append(skill.relative_to(REPO))
    if orphans:
        print("Orphan skill.md files (no matching .py):")
        for o in orphans:
            print(f"  - {o}")
        print()

    return 1 if (failed or orphans) else 0


if __name__ == "__main__":
    raise SystemExit(main())
