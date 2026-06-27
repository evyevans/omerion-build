"""Tools for HEALER — file I/O, Supabase queries, backup, audit trail.

Hard guardrails enforced at this layer:
  - validate_target_resource() raises on any .py path — mirrors AUDITOR rule
    CORE_LOGIC_MUTATION. Called before every write operation.
  - backup_file() must succeed before any file is written; returns the backup path.
  - write_audit_log() must be called after every apply (success or skip).
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.agents.healer.tools")

# Root of the omerion package on disk — used for relative path resolution.
_OMERION_ROOT = Path(__file__).resolve().parent.parent.parent  # .../omerion/
_BACKUPS_DIR  = _OMERION_ROOT / "_backups"

ALLOWED_EXTENSIONS = {".yaml", ".yml", ".md"}


def validate_target_resource(resource: str) -> None:
    """Raise ValueError if resource is a .py file (CORE_LOGIC_MUTATION guard)."""
    path = Path(resource)
    if path.suffix == ".py":
        raise ValueError(
            f"CORE_LOGIC_MUTATION: HEALER may not modify Python files. "
            f"Attempted: {resource}"
        )
    if path.suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"HEALER may only modify {ALLOWED_EXTENSIONS}. Got: {resource}"
        )


def backup_file(resource: str) -> str:
    """Copy resource to _backups/{stem}.bak.{timestamp}{suffix}. Returns backup path."""
    src = _OMERION_ROOT / resource
    if not src.exists():
        raise FileNotFoundError(f"backup_file: source not found: {src}")
    _BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = _BACKUPS_DIR / f"{src.stem}.bak.{ts}{src.suffix}"
    shutil.copy2(src, backup)
    log.info("healer_backup_created", src=str(src), backup=str(backup))
    return str(backup)


def patch_yaml_config(dotted_key: str, new_value: Any) -> tuple[str, str]:
    """Set a dotted key in config/agents.yaml using ruamel (preserves comments).

    Returns (diff_summary, before_content) so the caller can store before_content
    in audit_log for lossless restore without needing the local _backups/ path.

    Raises KeyError if the dotted_key path doesn't exist (no silent creation).
    Raises ValueError if the patched YAML fails to re-parse.
    """
    resource = "config/agents.yaml"
    validate_target_resource(resource)
    config_path = _OMERION_ROOT / resource

    before_content = config_path.read_text()

    from ruamel.yaml import YAML as RuamelYAML
    ryaml = RuamelYAML()
    ryaml.preserve_quotes = True
    data = ryaml.load(before_content)

    keys = dotted_key.split(".")
    node = data
    for k in keys[:-1]:
        node = node[k]   # raises KeyError intentionally if path missing

    old_value = node[keys[-1]]
    node[keys[-1]] = new_value

    buf = StringIO()
    ryaml.dump(data, buf)
    patched_str = buf.getvalue()

    try:
        ryaml.load(patched_str)
    except Exception as exc:
        raise ValueError(
            f"patch_yaml_config: patched YAML is invalid — aborting. Error: {exc}"
        ) from exc

    config_path.write_text(patched_str)

    diff_summary = f"config/agents.yaml: {dotted_key}: {old_value!r} -> {new_value!r}"
    log.info("healer_yaml_patched", key=dotted_key, old=old_value, new=new_value)
    return diff_summary, before_content


def patch_skill_md(skill_name: str, new_content: str) -> str:
    """Overwrite skills/{skill_name}.skill.md with new_content. Returns diff summary."""
    resource = f"skills/{skill_name}.skill.md"
    validate_target_resource(resource)
    skill_path = _OMERION_ROOT / resource

    if not skill_path.exists():
        raise FileNotFoundError(f"patch_skill_md: skill not found: {skill_path}")

    old_lines = skill_path.read_text().count("\n")
    skill_path.write_text(new_content)
    new_lines = new_content.count("\n")

    diff_summary = (
        f"skills/{skill_name}.skill.md: rewrote system prompt "
        f"({old_lines} -> {new_lines} lines)"
    )
    log.info("healer_skill_patched", skill=skill_name)
    return diff_summary


def load_agent_telemetry(agent_name: str, hours: int = 6) -> list[dict[str, Any]]:
    """Return recent agent_telemetry rows for the failing agent."""
    try:
        resp = (
            supabase.table("agent_telemetry")
            .select("node_name,status,duration_ms,cost_usd,error,started_at")
            .eq("agent_name", agent_name)
            .order("started_at", desc=True)
            .limit(50)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning("healer_load_telemetry_failed", agent=agent_name, error=str(exc))
        return []


def load_error_samples(agent_name: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return recent error_log rows mentioning the failing agent."""
    try:
        resp = (
            supabase.table("error_log")
            .select("message,traceback,occurred_at")
            .ilike("message", f"%{agent_name}%")
            .order("occurred_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning("healer_load_errors_failed", agent=agent_name, error=str(exc))
        return []


def load_recent_runs(agent_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent agent_runs rows for the failing agent."""
    try:
        resp = (
            supabase.table("agent_runs")
            .select("run_id,status,error,cost_usd,created_at,finished_at")
            .eq("agent_name", agent_name)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning("healer_load_runs_failed", agent=agent_name, error=str(exc))
        return []


def load_config_section(agent_name: str) -> dict[str, Any]:
    """Return the agents.yaml section for a specific agent (or empty dict)."""
    config_path = _OMERION_ROOT / "config" / "agents.yaml"
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
        return data.get("agents", {}).get(agent_name, {})
    except Exception as exc:
        log.warning("healer_load_config_failed", agent=agent_name, error=str(exc))
        return {}


def load_skill_file(skill_name: str) -> str:
    """Return the content of skills/{skill_name}.skill.md (empty string on miss)."""
    skill_path = _OMERION_ROOT / "skills" / f"{skill_name}.skill.md"
    if not skill_path.exists():
        return ""
    return skill_path.read_text()


def count_recent_healer_fixes(agent_name: str) -> int:
    """Query healer_recent_fixes view. Returns fix count in the last hour (0 on error)."""
    try:
        resp = (
            supabase.table("healer_recent_fixes")
            .select("fix_count")
            .eq("failing_agent", agent_name)
            .execute()
        )
        rows = resp.data or []
        return int(rows[0]["fix_count"]) if rows else 0
    except Exception as exc:
        log.warning("healer_loop_check_failed", agent=agent_name, error=str(exc))
        return 0  # fail open — don't block a fix if the view is unavailable


def load_rag_context(agent_name: str, config_key: str | None = None) -> list[str]:
    """Query omerion-legion-rag for architectural context about this agent/config.

    Returns up to 3 text snippets from the knowledge base.
    Falls back to empty list silently if Pinecone or OpenAI is unavailable.
    """
    if not getattr(settings, "pinecone_api_key", None):
        return []
    try:
        from omerion_core.llm.embeddings import embed

        query = f"{agent_name} {config_key or ''}".strip()
        vector = embed(query)

        index = pinecone_index()
        result = index.query(
            vector=vector,
            top_k=3,
            include_metadata=True,
            filter={"agent_id": {"$eq": agent_name}},
            namespace="architecture",
        )
        snippets = []
        for match in result.matches:
            if match.score > 0.5 and match.metadata.get("text"):
                snippets.append(match.metadata["text"][:500])
        return snippets
    except Exception as exc:
        log.warning("healer_rag_failed", agent=agent_name, error=str(exc))
        return []


def embed_architecture_outcome(
    *,
    failing_agent: str,
    session_id: str,
    root_cause: str | None,
    remediation_type: str | None,
    patch_description: str | None,
    heal_outcome: str,
) -> bool:
    """WRITE side of healer's RAG loop — index a heal event into `architecture`.

    `load_rag_context()` reads this back on the next failure of the same agent
    (filter agent_id == failing_agent, consumes metadata["text"]). Without this
    write the namespace stays empty and architecture RAG always returns nothing —
    which is exactly the gap this closes.

    Mandatory schema (Knowledge Triad): baseline {agent_id, department, namespace,
    run_date} + agent_type, failure_pattern, heal_action, heal_outcome,
    mutation_type. Dual-threshold dedup on write (0.96 hard / 0.90 soft). Vector id
    is deterministic on (failing_agent, session_id) so replays are idempotent.
    Fails open — returns False on skip/error, never raises into the graph.

    Note: agent_id = the *healed* agent (so the existing read path finds it);
    agent_type = "healer" (the producing agent, per pinecone_client convention).
    """
    if not root_cause:
        return False  # no diagnosis → nothing worth remembering
    if not getattr(settings, "pinecone_api_key", None):
        return False
    try:
        from datetime import date as _date

        from omerion_core.llm.embeddings import embed

        mutation_type = remediation_type or "none"
        heal_action = (patch_description or remediation_type or "no_action")[:300]
        text = (
            f"agent:{failing_agent} failure:{root_cause} "
            f"action:{mutation_type} -> {heal_action} outcome:{heal_outcome}"
        )
        vector = embed(text)
        idx = pinecone_index()
        vector_id = f"heal:{failing_agent}:{session_id}"

        # Dual-threshold dedup, scoped to this agent's architecture history.
        existing = idx.query(
            vector=vector, top_k=5, namespace="architecture",
            filter={"agent_id": {"$eq": failing_agent}}, include_metadata=True,
        )
        is_apparent_dup = False
        if existing.matches:
            best = existing.matches[0].score
            best_id = existing.matches[0].id
            if best_id != vector_id:  # self-match (replay) is never a duplicate
                if best >= 0.96:
                    log.info("healer_arch_hard_dedup_skip", agent=failing_agent, score=best)
                    return False
                elif best >= 0.90:
                    is_apparent_dup = True
                    log.info("healer_arch_soft_dedup_flag", agent=failing_agent, score=best)

        metadata: dict = {
            # ── Mandatory baseline ──
            "agent_id":        failing_agent,          # read path filters on this
            "department":      "infrastructure",
            "namespace":       "architecture",
            "run_date":        _date.today().isoformat(),
            # ── Architecture-namespace mandated fields ──
            "agent_type":      "healer",               # the producing agent
            "failure_pattern": root_cause[:300],
            "heal_action":     heal_action,
            "heal_outcome":    heal_outcome,           # resolved | escalated | failed
            "mutation_type":   mutation_type,          # config_patch | prompt_update | none | escalated
            # ── Consumed by load_rag_context() ──
            "text":            text[:1000],
        }
        if is_apparent_dup:
            metadata["is_apparent_duplicate"] = True

        idx.upsert(
            vectors=[{"id": vector_id, "values": vector, "metadata": metadata}],
            namespace="architecture",
        )
        log.info("healer_arch_written", agent=failing_agent, outcome=heal_outcome)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("healer_arch_write_failed", agent=failing_agent, error=str(exc))
        return False


# Vault roots, in resolution priority. `obsidian-vault` is the canonical second
# brain (per-agent kebab folders, 25+ files); `vault` is the legacy Infrastructure
# tree healer historically read. Resolving across both removes the "reader points
# at the wrong root" fragility without a destructive file migration (the two roots
# still hold divergent, differently-named infra sets — reconcile by hand, not here).
_VAULT_ROOTS = ("obsidian-vault", "vault", "obsidian/vault")


def _resolve_vault_file(filename: str):
    """Return the first existing vault file across the canonical roots, or None."""
    base = _OMERION_ROOT.parent
    for root in _VAULT_ROOTS:
        candidate = base / root / filename
        if candidate.exists():
            return candidate
    return None


def load_obsidian_section(filename: str, section: str) -> str:
    """Read a specific ## section from a vault file. Returns empty string on miss.

    Resolves `filename` across the canonical vault roots (obsidian-vault → vault →
    obsidian/vault), reading only the requested section (## header to next ##
    header). Caps output at 400 tokens (~1 600 chars) per the Knowledge Triad budget.
    """
    file_path = _resolve_vault_file(filename)
    if file_path is None:
        log.warning("healer_obsidian_miss", file=filename, section=section)
        return ""
    try:
        lines = file_path.read_text().splitlines()
        capturing = False
        result: list[str] = []
        for line in lines:
            if line.startswith("## ") and section in line:
                capturing = True
                result.append(line)
                continue
            if capturing:
                if line.startswith("## ") and section not in line:
                    break
                result.append(line)
        content = "\n".join(result)
        return content[:1_600]   # ~400 tokens
    except Exception as exc:
        log.warning("healer_obsidian_read_failed", file=filename, error=str(exc))
        return ""


def write_audit_log(
    *,
    source_agent: str,
    action_type: str,
    target_resource: str,
    diff_summary: str,
    raw_payload: dict[str, Any],
    before_content: str | None = None,
    hitl_review_id: str | None = None,
    audit_id: UUID | None = None,
) -> UUID:
    """Upsert a row into audit_log. Returns the audit_id.

    Accepts an optional caller-supplied audit_id for checkpoint-replay idempotency.
    If omitted, a fresh UUID is generated. Upsert on audit_id prevents
    duplicate rows when apply_fix is replayed from a LangGraph checkpoint.
    """
    if audit_id is None:
        audit_id = uuid4()
    row: dict[str, Any] = {
        "audit_id":        str(audit_id),
        "source_agent":    source_agent,
        "action_type":     action_type,
        "target_resource": target_resource,
        "diff_summary":    diff_summary[:2000],
        "raw_payload":     raw_payload,
        "before_content":  before_content,
    }
    if hitl_review_id:
        row["hitl_review_id"] = hitl_review_id
    supabase.table("audit_log").upsert(row, on_conflict="audit_id").execute()
    log.info("healer_audit_logged", audit_id=str(audit_id), action=action_type)
    return audit_id


def write_healer_action(
    *,
    run_id: str,
    audit_id: str | None,
    failing_agent: str,
    severity: str,
    metric: str,
    metric_value: float,
    root_cause: str | None,
    remediation_type: str | None,
    fix_applied: bool,
    healing_notes: str,
) -> None:
    """Insert a summary row into healer_actions for observability."""
    row: dict[str, Any] = {
        "run_id":           run_id,
        "audit_id":         audit_id,
        "failing_agent":    failing_agent,
        "severity":         severity,
        "metric":           metric,
        "metric_value":     metric_value,
        "root_cause":       root_cause,
        "remediation_type": remediation_type,
        "fix_applied":      fix_applied,
        "healing_notes":    healing_notes[:2000],
    }
    try:
        # Upsert on run_id — one action row per session; safe to replay from checkpoint.
        supabase.table("healer_actions").upsert(row, on_conflict="run_id").execute()
    except Exception as exc:
        log.warning("healer_action_write_failed", error=str(exc))
