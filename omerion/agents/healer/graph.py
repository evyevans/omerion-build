"""LangGraph for HEALER — Autonomous Remediation Engine (RSI Agent #16).

Flow:
    diagnose_root_cause
      -> formulate_remediation
           -> hitl_review  (only when severity==critical AND confidence<0.70 AND attempts>=2)
           -> hitl_wait    (interrupt checkpoint — founder approves/rejects)
      -> apply_fix          (backup -> patch -> write audit_log)
      -> emit_healing_status (healer_actions row + healing.applied event)
"""
from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .prompts import (
    DIAGNOSE_SYSTEM,
    DIAGNOSE_USER,
    FORMULATE_SYSTEM,
    FORMULATE_USER,
)
from .state import HealerState
from .tools import (
    backup_file,
    count_recent_healer_fixes,
    embed_architecture_outcome,
    load_agent_telemetry,
    load_config_section,
    load_error_samples,
    load_obsidian_section,
    load_rag_context,
    load_recent_runs,
    load_skill_file,
    patch_skill_md,
    patch_yaml_config,
    validate_target_resource,
    write_audit_log,
    write_healer_action,
)

log = get_logger("omerion.agents.healer")
_llm = ClaudeRouter()


def _fmt_block(items: list[dict[str, Any]], limit: int = 10) -> str:
    if not items:
        return "(none)"
    return json.dumps(items[:limit], indent=2, default=str)


# ── Node 0: loop_check ───────────────────────────────────────────────────────

@traced_node("healer.loop_check")
def loop_check(state: HealerState) -> dict[str, Any]:
    """Deterministic gate — no LLM. Queries healer_recent_fixes view."""
    count = count_recent_healer_fixes(state.failing_agent)
    log.info(
        "healer_loop_check",
        agent=state.failing_agent,
        recent_fix_count=count,
        loop_guard_active=(count >= 2),
    )
    return {"recent_fix_count": count}


def _loop_or_diagnose(state: HealerState) -> str:
    if state.loop_guard_active:
        log.warning(
            "healer_loop_guard_triggered",
            agent=state.failing_agent,
            recent_fix_count=state.recent_fix_count,
        )
        return "hitl_review"
    return "diagnose_root_cause"


# ── Node 1: diagnose_root_cause ───────────────────────────────────────────────

@traced_node("healer.diagnose")
def diagnose_root_cause(state: HealerState) -> dict[str, Any]:
    telemetry = load_agent_telemetry(state.failing_agent)
    errors    = load_error_samples(state.failing_agent)
    runs      = load_recent_runs(state.failing_agent)
    config    = load_config_section(state.failing_agent)
    rag_hits  = load_rag_context(state.failing_agent, state.patch_yaml_key)
    rag_block = "\n\n".join(rag_hits) if rag_hits else "(no architecture context found)"

    # Obsidian vault: canonical thresholds + loop-guard policy (≤400 tokens each)
    obsidian_thresholds = load_obsidian_section(
        "Infrastructure/healer/diagnostic-thresholds.md", "RSI Threshold Matrix"
    ) or "(vault file not yet synced — use skill.md thresholds)"
    obsidian_loop_guard = load_obsidian_section(
        "Infrastructure/healer/loop-guard-policy.md", "Loop Guard Escalation Ladder"
    ) or "(vault file not yet synced)"

    user_prompt = DIAGNOSE_USER.format(
        failing_agent=state.failing_agent,
        severity=state.severity,
        metric=state.metric,
        metric_value=state.metric_value,
        alert_run_id=state.alert_run_id or "n/a",
        obsidian_thresholds=obsidian_thresholds,
        obsidian_loop_guard=obsidian_loop_guard,
        telemetry_block=_fmt_block(telemetry),
        error_block=_fmt_block(errors),
        runs_block=_fmt_block(runs),
        config_block=json.dumps(config, indent=2),
        rag_block=rag_block,
    )

    resp = _llm.complete(
        tier=Tier.DEFAULT,                       # Sonnet (was the non-existent Tier.STANDARD)
        system=DIAGNOSE_SYSTEM,
        prompt=user_prompt,
        max_tokens=1024,
    )

    # complete() returns a dict {"text", ...}; parse the text (was json.loads on the dict).
    parsed, ok = extract_json_object(resp.get("text", ""))
    if not ok:
        log.warning("healer_diagnose_parse_failed", raw=str(resp.get("text", ""))[:200])
        parsed = {
            "root_cause": "parse_error",
            "confidence": 0.0,
            "recommended_remediation": "escalate",
        }

    return {
        "recent_telemetry":     telemetry,
        "error_samples":        errors,
        "recent_runs":          runs,
        "rag_context_hits":     rag_hits,
        "root_cause":           parsed.get("root_cause"),
        "diagnosis_confidence": float(parsed.get("confidence", 0.0)),
        "remediation_type":     parsed.get("recommended_remediation"),
        "target_resource":      parsed.get("target_resource"),
        "patch_yaml_key":       parsed.get("patch_yaml_key"),
        "patch_yaml_value":     parsed.get("patch_yaml_value"),
        "diagnosis_attempts":   state.diagnosis_attempts + 1,
    }


# ── Node 2: formulate_remediation ─────────────────────────────────────────────

@traced_node("healer.formulate")
def formulate_remediation(state: HealerState) -> dict[str, Any]:
    if state.remediation_type == "escalate" or state.requires_hitl_escalation:
        return {"remediation_type": "escalated", "patch_description": "escalated to HITL"}

    config = load_config_section(state.failing_agent)
    skill_name = state.failing_agent.replace("_", "-")
    skill_content = (
        load_skill_file(skill_name) if state.remediation_type == "prompt_update" else ""
    )

    obsidian_mutations = load_obsidian_section(
        "Infrastructure/healer/allowed-mutations.md", "Allowed Mutation Surfaces"
    ) or "(vault file not yet synced — only agents.yaml and *.skill.md are permitted)"

    user_prompt = FORMULATE_USER.format(
        failing_agent=state.failing_agent,
        root_cause=state.root_cause or "unknown",
        remediation_type=state.remediation_type,
        target_resource=state.target_resource or "n/a",
        obsidian_mutations=obsidian_mutations,
        config_block=json.dumps(config, indent=2),
        skill_block=skill_content or "(not applicable)",
    )

    resp = _llm.complete(
        tier=Tier.DEFAULT,                       # Sonnet (was the non-existent Tier.STANDARD)
        system=FORMULATE_SYSTEM,
        prompt=user_prompt,
        max_tokens=2048,
    )

    parsed, ok = extract_json_object(resp.get("text", ""))
    if not ok:
        log.warning("healer_formulate_parse_failed", raw=str(resp.get("text", ""))[:200])
        return {"remediation_type": "escalated", "patch_description": "formulation_parse_error"}

    return {
        "patch_description":   parsed.get("patch_description"),
        "patch_yaml_key":      parsed.get("patch_yaml_key") or state.patch_yaml_key,
        "patch_yaml_value":    parsed.get("patch_yaml_value") or state.patch_yaml_value,
        "patch_skill_content": parsed.get("patch_skill_content"),
        "diagnosis_confidence": max(
            state.diagnosis_confidence,
            float(parsed.get("confidence", state.diagnosis_confidence)),
        ),
    }


# ── Node 3a: hitl_review ──────────────────────────────────────────────────────

@traced_node("healer.hitl_review")
def hitl_review(state: HealerState) -> dict[str, Any]:
    result = create_founder_review_task(
        agent_name="healer",
        session_id=state.session_id,
        subject=(
            f"HEALER self-patch approval — `{state.failing_agent}` "
            f"({state.remediation_type or 'loop-guard'})"
        ),
        context_md=(
            f"**Failing agent:** `{state.failing_agent}`\n"
            f"**Severity:** {state.severity}\n"
            f"**Metric:** {state.metric} = {state.metric_value}\n"
            f"**Root cause:** {state.root_cause or 'unknown'}\n"
            f"**Diagnosis confidence:** {state.diagnosis_confidence:.0%} after "
            f"{state.diagnosis_attempts} attempt(s)\n"
            f"**Proposed remediation:** {state.remediation_type}\n"
            f"**Target:** `{state.target_resource}`\n"
            f"**Patch:** {state.patch_description}\n\n"
            f"⚠️ HEALER wants to modify production (config / skill prompt). A backup is "
            f"taken before the patch is written. **Approve** to apply, **reject** to skip."
        ),
        draft_ref={
            "failing_agent":    state.failing_agent,
            "severity":         state.severity,
            "metric":           state.metric,
            "metric_value":     state.metric_value,
            "root_cause":       state.root_cause,
            "remediation_type": state.remediation_type,
            "target_resource":  state.target_resource,
            "patch_yaml_key":   state.patch_yaml_key,
            "patch_yaml_value": state.patch_yaml_value,
        },
        correlation_id=state.session_id,
    )
    return {"review_id": result["review_id"]}


# ── Node 3b: hitl_wait ────────────────────────────────────────────────────────

@traced_node("healer.hitl_wait")
def hitl_wait(state: HealerState) -> dict[str, Any]:
    # The resume payload is {"decisions": {review_id: decision}} — extract the
    # actual verdict. (Previously the whole dict was stored in hitl_decision, so
    # apply_fix's `== "rejected"` check never matched and a rejected patch applied.)
    result = interrupt({"review_id": str(state.review_id), "session_id": state.session_id})
    decisions = result.get("decisions", {}) if isinstance(result, dict) else {}
    decision = decisions.get(str(state.review_id), "rejected")
    return {"hitl_decision": decision}


# ── Node 4: apply_fix ─────────────────────────────────────────────────────────

@traced_node("healer.apply_fix")
def apply_fix(state: HealerState) -> dict[str, Any]:
    skip_reasons = [
        state.remediation_type == "escalated",
        state.hitl_decision == "rejected",
        not state.target_resource,
    ]
    if any(skip_reasons):
        return {
            "fix_applied":  False,
            "healing_notes": f"patch skipped — {state.remediation_type or 'no target'}",
        }

    try:
        validate_target_resource(state.target_resource)
        backup_path = backup_file(state.target_resource)

        before_content: str | None = None
        if state.remediation_type == "config_patch":
            diff_summary, before_content = patch_yaml_config(
                state.patch_yaml_key, state.patch_yaml_value
            )
        elif state.remediation_type == "prompt_update" and state.patch_skill_content:
            skill_name = state.failing_agent.replace("_", "-")
            before_content = load_skill_file(skill_name)
            diff_summary = patch_skill_md(skill_name, state.patch_skill_content)
        else:
            return {"fix_applied": False, "healing_notes": "unknown remediation_type"}

        audit_id = write_audit_log(
            source_agent="healer",
            action_type=state.remediation_type,
            target_resource=state.target_resource,
            diff_summary=diff_summary,
            before_content=before_content,
            raw_payload={
                "failing_agent":    state.failing_agent,
                "severity":         state.severity,
                "metric":           state.metric,
                "metric_value":     state.metric_value,
                "root_cause":       state.root_cause,
                "patch_yaml_key":   state.patch_yaml_key,
                "patch_yaml_value": state.patch_yaml_value,
                "backup_path":      backup_path,
                "hitl_review_id":   str(state.review_id) if state.review_id else None,
            },
            hitl_review_id=str(state.review_id) if state.review_id else None,
        )

        return {
            "fix_applied":   True,
            "audit_id":      audit_id,
            "backup_path":   backup_path,
            "healing_notes": diff_summary,
        }

    except Exception as exc:
        log.error("healer_apply_fix_failed", error=str(exc))
        return {
            "fix_applied":   False,
            "healing_notes": f"apply_fix exception: {exc}",
        }


# ── Node 5: emit_healing_status ───────────────────────────────────────────────

@traced_node("healer.emit")
def emit_healing_status(state: HealerState) -> dict[str, Any]:
    write_healer_action(
        run_id=state.session_id,
        audit_id=str(state.audit_id) if state.audit_id else None,
        failing_agent=state.failing_agent,
        severity=state.severity,
        metric=state.metric,
        metric_value=state.metric_value,
        root_cause=state.root_cause,
        remediation_type=state.remediation_type,
        fix_applied=state.fix_applied,
        healing_notes=state.healing_notes,
    )

    # Write-back to the `architecture` Pinecone namespace (continuous-improvement
    # loop). heal_outcome is derived from the terminal state: a written fix is
    # resolved; an escalation/rejection is escalated; anything else failed.
    if state.fix_applied:
        heal_outcome = "resolved"
    elif (
        state.remediation_type == "escalated"
        or state.requires_hitl_escalation
        or state.hitl_decision == "rejected"
    ):
        heal_outcome = "escalated"
    else:
        heal_outcome = "failed"
    embed_architecture_outcome(
        failing_agent=state.failing_agent,
        session_id=str(state.session_id),
        root_cause=state.root_cause,
        remediation_type=state.remediation_type,
        patch_description=state.patch_description,
        heal_outcome=heal_outcome,
    )

    emit_event(
        event_type=EventType.HEALING_APPLIED.value,
        source_agent="healer",
        payload={
            "healed_agent":     state.failing_agent,
            "remediation_type": state.remediation_type or "none",
            "audit_id":         str(state.audit_id) if state.audit_id else None,
            "fix_applied":      state.fix_applied,
            "correlation_id":   str(state.session_id),
            "idempotency_key":  f"healing.applied:{state.failing_agent}:{state.session_id}",
        },
        correlation_id=state.session_id,
    )

    return {}


# ── Conditional routing ───────────────────────────────────────────────────────

def _needs_hitl(state: HealerState) -> str:
    # G3 — EVERY real self-modification (config or skill-prompt patch) requires
    # founder approval before it is written to production. Only no-op outcomes
    # (escalated / no actionable target) skip the gate.
    if state.remediation_type in ("config_patch", "prompt_update") and state.target_resource:
        return "hitl_review"
    return "apply_fix"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build():
    """Compile and return the HEALER LangGraph graph."""
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(HealerState)

    g.add_node("loop_check",            loop_check)
    g.add_node("diagnose_root_cause",   diagnose_root_cause)
    g.add_node("formulate_remediation", formulate_remediation)
    g.add_node("hitl_review",           hitl_review)
    g.add_node("hitl_wait",             hitl_wait)
    g.add_node("apply_fix",             apply_fix)
    g.add_node("emit_healing_status",   emit_healing_status)

    g.set_entry_point("loop_check")
    g.add_conditional_edges("loop_check", _loop_or_diagnose, {
        "diagnose_root_cause": "diagnose_root_cause",
        "hitl_review":         "hitl_review",
    })
    g.add_edge("diagnose_root_cause",   "formulate_remediation")
    g.add_conditional_edges("formulate_remediation", _needs_hitl, {
        "hitl_review": "hitl_review",
        "apply_fix":   "apply_fix",
    })
    g.add_edge("hitl_review",         "hitl_wait")
    g.add_edge("hitl_wait",           "apply_fix")
    g.add_edge("apply_fix",           "emit_healing_status")
    g.add_edge("emit_healing_status", END)

    return g.compile(checkpointer=get_checkpointer())
