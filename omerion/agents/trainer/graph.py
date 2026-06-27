"""LangGraph for TRAINER (Wave 5 v2).

Flow (5 nodes — added shadow_evaluate between generate and propose):

    fetch_outcomes
        │
        ▼
    identify_underperforming_prompts
        │
        ├──[no_signal]──▶ END
        │
        ▼ [generate]
    generate_prompt_improvements   ← samples cohorts, clusters failures,
        │                            calls LLM with evidence pack
        ▼
    shadow_evaluate                ← NEW (v2): replay vs failure + success
        │                            cohorts; compute fix/regression rates
        ▼
    propose_update                 ← routes by ShadowEvalResult:
        │                            auto-promote / amber / auto-reject /
        ▼                            insufficient-data
       END

Decision matrix (route_proposal from shadow_eval.py):
  auto_promote              → founder HITL with 🟢 badge
  review_amber              → founder HITL with 🟡 badge + regression samples
  review_insufficient_data  → founder HITL with ⚪ badge (no measurement)
  auto_reject_low_fix       → status='rejected', no HITL noise, log only
  auto_reject_high_regress  → status='rejected', no HITL noise, log only

Idempotency:
  * Run-level: agent_wrapper minute-window dedupe
  * Proposal-level: DB UNIQUE on (agent, constant, iso_week)
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .clustering import ClusterReport, cluster_failures
from .shadow_eval import (
    ShadowEvalResult,
    route_proposal,
    sample_failure_cohort,
    sample_success_cohort,
    shadow_evaluate,
)
from .state import (
    AgentPerformance,
    PromptProposal,
    TrainerState,
    UnderperformingAgent,
)
from .tools import (
    apply_prompt_update,
    fetch_performance_window,
    generate_improvement,
    identify_underperformers,
    iso_week_key,
    persist_proposal,
    read_agent_prompts,
    sha256_hex,
    update_proposal_decision,
)

log = get_logger("omerion.agents.trainer")


# ─────────────────────────── Node 1 ───────────────────────────────────

@traced_node("fetch_outcomes")
def fetch_outcomes_node(state: TrainerState) -> TrainerState:
    """Aggregate last-7d performance for the 6 target agents."""
    state.iso_week = iso_week_key(state.run_date)
    state.performance_summaries = fetch_performance_window(days=state.window_days)
    log.info(
        "trainer_fetched_outcomes",
        agents_with_data=len(state.performance_summaries),
        iso_week=state.iso_week,
    )
    if not state.performance_summaries:
        state.no_signal = True
        log.info("trainer_no_signal_skip", iso_week=state.iso_week)
    return state


# ─────────────────────────── Node 2 ───────────────────────────────────

@traced_node("identify_underperforming_prompts")
def identify_node(state: TrainerState) -> TrainerState:
    """Apply deterministic thresholds + AST-snapshot each underperformer's prompts."""
    flagged = identify_underperformers(state.performance_summaries)
    if not flagged:
        state.no_signal = True
        log.info("trainer_no_underperformers", checked=len(state.performance_summaries))
        return state

    metrics_by_name: dict[str, AgentPerformance] = {
        m.agent_name: m for m in state.performance_summaries
    }
    underperformers: list[UnderperformingAgent] = []
    for agent_name, signal in flagged:
        prompts = read_agent_prompts(agent_name)
        if not prompts:
            log.warning("trainer_skipping_unparseable_prompts", agent=agent_name)
            continue
        underperformers.append(UnderperformingAgent(
            agent_name=agent_name,
            failure_signal=signal,
            current_prompts=prompts,
            metrics=metrics_by_name[agent_name],
        ))
    state.underperformers = underperformers
    log.info(
        "trainer_identified_underperformers",
        count=len(underperformers),
        agents=[u.agent_name for u in underperformers],
    )
    if not underperformers:
        state.no_signal = True
    return state


def _route_after_identify(state: TrainerState) -> str:
    return "end" if state.no_signal else "generate"


# ─────────────────────────── Node 3 ───────────────────────────────────
# Wave 5 v2.5: now also samples cohorts + clusters failures so the LLM
# gets concrete evidence, not aggregates.

@traced_node("generate_prompt_improvements")
def generate_node(state: TrainerState) -> TrainerState:
    """Per (underperformer × prompt constant):
      1. Sample failure cohort from prompt_invocations
      2. DBSCAN-cluster the failed inputs
      3. Sample success cohort (small — 3 for the LLM context)
      4. Call LLM with evidence pack + house-style few-shot
      5. Stash samples & clusters on the proposal for shadow_eval to reuse
    """
    MAX_PROMPTS_PER_AGENT = 2

    router = ClaudeRouter()
    proposals: list[PromptProposal] = []

    # Stash per-proposal evidence so shadow_evaluate (next node) can
    # reuse the exact samples without re-querying.
    state_evidence: dict[str, dict[str, Any]] = {}

    for ua in state.underperformers:
        ordered = sorted(
            ua.current_prompts.items(),
            key=lambda kv: (0 if kv[0].endswith("_SYSTEM") else 1, kv[0]),
        )[:MAX_PROMPTS_PER_AGENT]

        for constant_name, current_text in ordered:
            prompt_sha = sha256_hex(current_text)

            # Sample evidence cohorts.
            failure_samples = sample_failure_cohort(
                agent_name=ua.agent_name,
                prompt_constant_name=constant_name,
                prompt_sha256=prompt_sha,
                days=state.window_days,
                n=10,
            )
            success_samples_small = sample_success_cohort(
                agent_name=ua.agent_name,
                prompt_constant_name=constant_name,
                prompt_sha256=prompt_sha,
                days=state.window_days,
                n=3,  # only 3 for the LLM context — shadow_eval pulls 30 separately
            )

            # Cluster the failures.
            cluster_report: ClusterReport = cluster_failures(failure_samples)
            failure_clusters_block = cluster_report.format_for_llm()

            proposal = generate_improvement(
                router,
                agent_name=ua.agent_name,
                prompt_constant_name=constant_name,
                current_text=current_text,
                failure_samples=failure_samples,
                success_samples=success_samples_small,
                failure_clusters_block=failure_clusters_block,
            )
            if proposal is None:
                continue
            proposals.append(proposal)

            # Stash by (agent, constant) so shadow_evaluate finds the
            # right evidence without re-querying.
            state_evidence[f"{ua.agent_name}::{constant_name}"] = {
                "cluster_report": cluster_report,
                "failure_samples_count": len(failure_samples),
            }

    state.proposals = proposals
    # Stash on TrainerState as a free-form dict so the next node has it.
    # State model allows extra fields (Pydantic ConfigDict extra='allow'
    # in AgentRunState? — we use it deliberately here).
    setattr(state, "_evidence", state_evidence)

    log.info(
        "trainer_proposals_generated",
        count=len(proposals),
        underperformers=len(state.underperformers),
    )
    if not proposals:
        state.no_signal = True
    return state


# ─────────────────────────── Node 3.5 — NEW (v2) ──────────────────────

@traced_node("shadow_evaluate")
def shadow_evaluate_node(state: TrainerState) -> TrainerState:
    """Replay each proposal vs. historical cohorts. Compute fix_rate /
    regression_rate / net_improvement. Route decision applied in Node 4.
    """
    router = ClaudeRouter()
    evaluations: dict[str, ShadowEvalResult] = {}

    for proposal in state.proposals:
        result = shadow_evaluate(
            router,
            agent_name=proposal.target_agent_name,
            prompt_constant_name=proposal.prompt_constant_name,
            prompt_sha256=proposal.current_text_sha256,
            proposed_text=proposal.proposed_text,
            days=state.window_days,
        )
        evaluations[f"{proposal.target_agent_name}::{proposal.prompt_constant_name}"] = result

        # Backfill the proposal's `confidence` and `expected_impact`
        # with the DETERMINISTIC measurement (replaces LLM self-report).
        proposal.confidence = max(0.0, min(1.0, result.net_improvement + 0.5))
        proposal.expected_impact = {
            **(proposal.expected_impact or {}),
            "shadow_eval": {
                "fix_rate": round(result.fix_rate, 3),
                "regression_rate": round(result.regression_rate, 3),
                "net_improvement": round(result.net_improvement, 3),
                "failure_cohort_size": result.failure_cohort_size,
                "success_cohort_size": result.success_cohort_size,
                "median_cost_delta_usd": round(result.median_cost_delta, 6),
                "insufficient_data": result.insufficient_data,
                "routing_decision": route_proposal(result),
            },
        }

    setattr(state, "_evaluations", evaluations)

    log.info(
        "trainer_shadow_eval_complete",
        proposals=len(state.proposals),
        decisions={
            k: v.get("shadow_eval", {}).get("routing_decision", "?")
            for k, v in [
                (p.prompt_constant_name, p.expected_impact)
                for p in state.proposals
            ]
        },
    )
    return state


# ─────────────────────────── Node 4 (v2 — branched) ───────────────────

# Discord embed length cap (text fields). The 3-panel card has to fit
# under this even with long prompts. Truncation thresholds tuned to keep
# the worst-case rendered card under 4000 chars total.
_EMBED_MAX_PANEL_CHARS = 900


def _build_diff_panel(current: str, proposed: str) -> str:
    """Minimal unified diff (line-level) for the HITL card.

    We deliberately don't run `difflib.unified_diff` — its output is
    noisy for prompts (every reflowed paragraph shows as a full rewrite).
    Instead, we present the two texts truncated, labeled, and let the
    founder visually compare. The shadow eval numbers carry the real
    decision-grade signal.
    """
    cur = current[:_EMBED_MAX_PANEL_CHARS]
    new = proposed[:_EMBED_MAX_PANEL_CHARS]
    return f"**Current:**\n```\n{cur}\n```\n**Proposed:**\n```\n{new}\n```"


def _build_shadow_panel(impact: dict[str, Any]) -> str:
    """The shadow-eval result block."""
    s = (impact or {}).get("shadow_eval") or {}
    if not s:
        return "_(no shadow eval — proposal predates v2)_"
    if s.get("insufficient_data"):
        return (
            f"⚪ **Insufficient history for shadow eval** — "
            f"have {s.get('failure_cohort_size', 0)} failures + "
            f"{s.get('success_cohort_size', 0)} successes."
        )
    badge = {
        "auto_promote":             "🟢 STRONG IMPROVEMENT",
        "review_amber":             "🟡 PARTIAL IMPROVEMENT",
        "review_insufficient_data": "⚪ NEEDS HUMAN JUDGMENT",
    }.get(s.get("routing_decision") or "", "🟡")
    return (
        f"{badge}\n"
        f"  Fix rate:        {s.get('fix_rate', 0) * 100:.0f}% "
        f"({int(round(s.get('fix_rate', 0) * s.get('failure_cohort_size', 0)))}/"
        f"{s.get('failure_cohort_size', 0)} historical failures resolved)\n"
        f"  Regression rate: {s.get('regression_rate', 0) * 100:.1f}% "
        f"of historical successes broken\n"
        f"  Net improvement: {s.get('net_improvement', 0) * 100:+.0f}pp\n"
        f"  Median cost Δ:   ${s.get('median_cost_delta_usd', 0):+.4f} per call"
    )


def _build_review_context_v2(
    proposal: PromptProposal,
    evidence: dict[str, Any] | None,
    routing_decision: str,
) -> str:
    """3-panel HITL embed (v2): shadow result + diff + rationale.

    Replaces v1's "huge two-text-block side-by-side." The founder reads:
      1. Did this objectively work? (shadow numbers)
      2. What changed? (diff panel)
      3. Why does the model think this helps? (rationale)
    """
    cluster_block = ""
    if evidence and "cluster_report" in evidence:
        report: ClusterReport = evidence["cluster_report"]
        cluster_block = (
            f"\n\n**🧪 FAILURE PATTERN** (from {report.total_failures} "
            f"historical failures):\n```\n{report.format_for_llm(max_clusters=2)[:600]}\n```"
        )

    return (
        f"**🟢 SHADOW EVAL**\n```\n{_build_shadow_panel(proposal.expected_impact)}\n```"
        f"{cluster_block}"
        f"\n\n**📝 DIFF** (`{proposal.prompt_constant_name}` on "
        f"`{proposal.target_agent_name}`):\n"
        f"{_build_diff_panel(proposal.current_text, proposal.proposed_text)}"
        f"\n\n**🔁 RATIONALE** (LLM-generated, founder verifies):\n"
        f"> {proposal.rationale[:500]}"
        f"\n\n_Routing: `{routing_decision}` — confidence "
        f"{proposal.confidence:.2f} (deterministic, from shadow eval)_"
    )


@traced_node("propose_update")
def propose_update_node(state: TrainerState) -> TrainerState:
    """Per proposal: route by shadow_eval verdict.

      auto_promote / review_amber / review_insufficient_data
          → persist + create HITL review + interrupt at end
      auto_reject_low_fix / auto_reject_high_regress
          → persist with status='rejected', skip HITL
    """
    review_to_improvement: dict[str, str] = {}
    proposal_by_review: dict[str, PromptProposal] = {}
    auto_rejected = 0
    evidence_map: dict[str, Any] = getattr(state, "_evidence", {}) or {}

    for proposal in state.proposals:
        impact = proposal.expected_impact or {}
        routing = (impact.get("shadow_eval") or {}).get("routing_decision") or "review_amber"

        # Auto-reject branches: persist row but don't bother the founder.
        if routing in ("auto_reject_low_fix", "auto_reject_high_regress"):
            improvement_id = persist_proposal(
                proposal,
                run_id=state.run_id or state.session_id,
                correlation_id=state.correlation_id and str(state.correlation_id),
                iso_week=state.iso_week,
            )
            if improvement_id:
                update_proposal_decision(
                    improvement_id,
                    decision="rejected",
                    founder_notes=f"auto-rejected by shadow eval: {routing}",
                )
                auto_rejected += 1
                log.info(
                    "trainer_auto_rejected",
                    improvement_id=improvement_id,
                    routing=routing,
                    fix_rate=impact.get("shadow_eval", {}).get("fix_rate"),
                    regression_rate=impact.get("shadow_eval", {}).get("regression_rate"),
                )
            continue

        # HITL branches: persist + create review.
        improvement_id = persist_proposal(
            proposal,
            run_id=state.run_id or state.session_id,
            correlation_id=state.correlation_id and str(state.correlation_id),
            iso_week=state.iso_week,
        )
        if not improvement_id:
            continue
        state.proposals_persisted += 1

        evidence = evidence_map.get(
            f"{proposal.target_agent_name}::{proposal.prompt_constant_name}"
        )
        try:
            review = create_founder_review_task(
                agent_name="trainer",
                session_id=state.session_id,
                subject=(
                    f"TRAINER {routing.replace('_', ' ')}: "
                    f"{proposal.target_agent_name} / {proposal.prompt_constant_name}"
                ),
                context_md=_build_review_context_v2(proposal, evidence, routing),
                draft_ref={
                    "improvement_id": improvement_id,
                    "target_agent": proposal.target_agent_name,
                    "prompt_constant": proposal.prompt_constant_name,
                    "current_text_sha256": proposal.current_text_sha256,
                    "routing": routing,
                    "shadow_eval": impact.get("shadow_eval"),
                },
                correlation_id=state.correlation_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("trainer_hitl_create_failed", improvement_id=improvement_id, error=str(exc))
            continue

        review_id = str(review["review_id"])
        review_to_improvement[review_id] = improvement_id
        proposal_by_review[review_id] = proposal
        state.review_ids.append(review["review_id"])

    state.proposals_rejected += auto_rejected

    if not review_to_improvement:
        log.info(
            "trainer_no_hitl_to_wait_on",
            auto_rejected=auto_rejected,
            total=len(state.proposals),
        )
        state.decision = "rejected" if auto_rejected else "pending"
        return state

    log.info(
        "trainer_hitl_pause",
        reviews=len(review_to_improvement),
        auto_rejected=auto_rejected,
    )

    resume_payload: Any = interrupt({
        "review_ids": list(review_to_improvement.keys()),
        "session_id": state.session_id,
        "agent": "trainer",
        "summary": (
            f"TRAINER produced {len(review_to_improvement)} proposal(s) "
            f"for review (plus {auto_rejected} auto-rejected by shadow "
            f"eval) for ISO week {state.iso_week}."
        ),
    })

    decisions: dict[str, str] = (
        resume_payload.get("decisions", {})
        if isinstance(resume_payload, dict)
        else {}
    )

    applied_updates: list[dict[str, Any]] = []
    for review_id_str, improvement_id in review_to_improvement.items():
        decision = decisions.get(review_id_str, "rejected")
        update_proposal_decision(improvement_id, decision=decision)
        if decision == "approved":
            state.proposals_approved += 1
            # Apply the approved prompt (founder-approved → AUDITOR Rule 3 passes).
            proposal = proposal_by_review.get(review_id_str)
            if proposal is not None:
                result = apply_prompt_update(
                    agent_name=proposal.target_agent_name,
                    prompt_constant_name=proposal.prompt_constant_name,
                    proposed_text=proposal.proposed_text,
                    expected_sha256=proposal.current_text_sha256,
                    hitl_review_id=review_id_str,
                )
                applied_updates.append({
                    "improvement_id": improvement_id,
                    "target_agent": proposal.target_agent_name,
                    "prompt_constant": proposal.prompt_constant_name,
                    "applied": result.get("applied", False),
                    "error": result.get("error"),
                })
                if not result.get("applied"):
                    log.warning("trainer_prompt_apply_skipped",
                                improvement_id=improvement_id, reason=result.get("error"))
        else:
            state.proposals_rejected += 1

    if state.proposals_approved and (state.proposals_rejected - auto_rejected) > 0:
        state.decision = "mixed"
    elif state.proposals_approved:
        state.decision = "approved"
    else:
        state.decision = "rejected"

    log.info(
        "trainer_run_complete",
        approved=state.proposals_approved,
        rejected=state.proposals_rejected,
        auto_rejected=auto_rejected,
        decision=state.decision,
    )

    # Closes the RSI loop: emit a dedicated PROMPT_UPDATE_APPLIED event ONLY for
    # prompts that were actually written to disk (founder-approved → applied).
    # AUDITOR consumes this to verify the self-modification (Rule 3 HITL_BYPASS:
    # the founder approval recorded in audit_log must exist). We do NOT reuse
    # RD_PROPOSAL_SUBMITTED — that routes to build-orchestrator, which is the
    # wrong consumer for a prompt edit.
    applied_ok = [u for u in applied_updates if u.get("applied")]
    if applied_ok:
        emit_event(
            EventType.PROMPT_UPDATE_APPLIED,
            source_agent="trainer",
            payload={
                "iso_week": state.iso_week,
                "applied_count": len(applied_ok),
                "skipped_count": len(applied_updates) - len(applied_ok),
                "targets": [
                    {"agent": u["target_agent"], "prompt": u["prompt_constant"]}
                    for u in applied_ok
                ],
                "decision": state.decision,
            },
            correlation_id=state.correlation_id,
        )

    return state


def _route_after_generate(state: TrainerState) -> str:
    """Skip shadow_evaluate if there are no proposals to evaluate."""
    return "end" if state.no_signal or not state.proposals else "shadow"


# ─────────────────────────── build ────────────────────────────────────

def build() -> Any:
    """Compile the TRAINER v2 graph."""
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(TrainerState)
    g.add_node("fetch", fetch_outcomes_node)
    g.add_node("identify", identify_node)
    g.add_node("generate", generate_node)
    g.add_node("shadow", shadow_evaluate_node)      # ← NEW in v2
    g.add_node("propose", propose_update_node)

    g.set_entry_point("fetch")
    g.add_edge("fetch", "identify")

    g.add_conditional_edges(
        "identify",
        _route_after_identify,
        {"generate": "generate", "end": END},
    )

    # NEW v2 edge: skip shadow if generate produced nothing.
    g.add_conditional_edges(
        "generate",
        _route_after_generate,
        {"shadow": "shadow", "end": END},
    )

    g.add_edge("shadow", "propose")
    g.add_edge("propose", END)

    return g.compile(checkpointer=get_checkpointer())
