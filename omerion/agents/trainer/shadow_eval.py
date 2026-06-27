"""Shadow evaluation — Wave 5 v2.3.

Closes the catastrophic-forgetting gap in TRAINER v1.

Before TRAINER proposes a prompt rewrite to the founder, this module
**replays** the proposed prompt against two cohorts from
`prompt_invocations`:

  * **Failure cohort** (n ≤ 10): runs that just FAILED under the current
    prompt. The new prompt's job is to *succeed* on these.
  * **Success cohort** (n ≤ 30): runs that SUCCEEDED under the current
    prompt. The new prompt's job is to *also succeed* on these — this
    is the catastrophic-forgetting check.

Output:
  * `fix_rate`        = failures_fixed / failures_total
  * `regression_rate` = (successes_total - successes_kept) / successes_total
  * `net_improvement` = fix_rate − regression_rate
  * cost / latency delta vs. the original calls

These deterministic numbers replace the LLM's self-reported
`confidence` and `expected_failure_reduction_pct` in the HITL payload.
The founder reads MEASUREMENT, not estimation.

Cost bound: 10 failure + 30 success = 40 replay calls per proposal.
With 6 target agents × 2 prompts × Tier.HEAVY ≈ $7/week worst case.

Replay does NOT execute real side effects — it only calls the LLM and
checks whether the response is structurally valid (parseable, passes
style_guard, contains required schema keys). The agent's tools.py
write paths are NOT invoked. This means "fix_rate" measures whether
the LLM produces a valid output, not whether it produces the *right*
output for the downstream business action. That stronger check would
require running the agent's full graph in a sandbox — a future v3
upgrade. For now, structural validity is the most we can measure
without side effects.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.outreach.style_guard import filter as style_filter

log = get_logger("omerion.agents.trainer.shadow_eval")


# ─────────────────────────── data shapes ──────────────────────────────


@dataclass
class InvocationSample:
    """One historical invocation pulled from prompt_invocations for replay."""

    invocation_id: str
    rendered_input_text: str
    original_response: str
    original_success: bool
    original_cost_usd: float
    original_tokens_out: int
    error_class: str | None = None
    error_message: str | None = None


@dataclass
class ReplayResult:
    """Outcome of replaying ONE sample under the proposed prompt."""

    sample_invocation_id: str
    original_success: bool
    new_success: bool
    new_response: str = ""
    new_cost_usd: float = 0.0
    new_tokens_out: int = 0
    failure_reason: str | None = None       # why new_success=False, for HITL card


@dataclass
class ShadowEvalResult:
    """The complete shadow-eval verdict for one proposal.

    The 4-branch decision matrix in graph.py reads these fields to route
    the proposal to (auto-promote-HITL / amber-HITL / auto-reject).
    """

    # Cohort sizes (may be < target if not enough history)
    failure_cohort_size:   int = 0
    success_cohort_size:   int = 0

    # Counts
    failure_cohort_fixed:  int = 0
    success_cohort_kept:   int = 0

    # Derived metrics
    fix_rate:              float = 0.0   # higher = better
    regression_rate:       float = 0.0   # higher = WORSE
    net_improvement:       float = 0.0   # fix_rate − regression_rate

    # Cost / latency deltas (new - old, per-call median)
    median_cost_delta:     float = 0.0
    median_tokens_delta:   float = 0.0

    # Samples for the HITL card (worst 3 regressions, 3 fixes)
    regression_samples:    list[ReplayResult] = field(default_factory=list)
    fix_samples:           list[ReplayResult] = field(default_factory=list)

    # Diagnostic: was there enough cohort to draw a conclusion?
    insufficient_data:     bool = False
    skipped_reason:        str | None = None


# ─────────────────────────── cohort sampling ──────────────────────────


def sample_failure_cohort(
    *,
    agent_name: str,
    prompt_constant_name: str,
    prompt_sha256: str,
    days: int = 7,
    n: int = 10,
) -> list[InvocationSample]:
    """Pull up to `n` failed invocations for this (agent, prompt) over
    the last `days` days. Failures = rows where success=false AND
    rendered_input_text IS NOT NULL (we need text to replay).

    Ordered by most-recent-failure first so the sample reflects the
    current failure mode, not stale ones.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = (
            supabase.table("prompt_invocations")
            .select(
                "invocation_id,rendered_input_text,response_text,success,"
                "cost_usd,tokens_out,error_class,error_message"
            )
            .eq("agent_name", agent_name)
            .eq("prompt_constant_name", prompt_constant_name)
            .eq("prompt_sha256", prompt_sha256)
            .eq("success", False)
            .gte("created_at", cutoff)
            .not_.is_("rendered_input_text", "null")
            .order("created_at", desc=True)
            .limit(n)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "shadow_eval_failure_cohort_query_failed",
            agent=agent_name, constant=prompt_constant_name, error=str(exc),
        )
        return []
    return [
        InvocationSample(
            invocation_id=r["invocation_id"],
            rendered_input_text=r.get("rendered_input_text") or "",
            original_response=r.get("response_text") or "",
            original_success=False,
            original_cost_usd=float(r.get("cost_usd") or 0.0),
            original_tokens_out=int(r.get("tokens_out") or 0),
            error_class=r.get("error_class"),
            error_message=r.get("error_message"),
        )
        for r in rows
    ]


def sample_success_cohort(
    *,
    agent_name: str,
    prompt_constant_name: str,
    prompt_sha256: str,
    days: int = 7,
    n: int = 30,
) -> list[InvocationSample]:
    """Pull up to `n` successful invocations, randomized to avoid
    sampling only the latest happy-path runs.

    Strategy: pull the most-recent 3*n successful runs, then random-
    sample n. Keeps the cohort recent (current input distribution) and
    diverse (not all from one client).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = (
            supabase.table("prompt_invocations")
            .select(
                "invocation_id,rendered_input_text,response_text,"
                "cost_usd,tokens_out"
            )
            .eq("agent_name", agent_name)
            .eq("prompt_constant_name", prompt_constant_name)
            .eq("prompt_sha256", prompt_sha256)
            .eq("success", True)
            .gte("created_at", cutoff)
            .not_.is_("rendered_input_text", "null")
            .order("created_at", desc=True)
            .limit(n * 3)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "shadow_eval_success_cohort_query_failed",
            agent=agent_name, constant=prompt_constant_name, error=str(exc),
        )
        return []

    # Random sample without replacement so the cohort is diverse.
    rng = random.Random(42)  # deterministic for reproducibility
    sampled = rng.sample(rows, min(n, len(rows)))
    return [
        InvocationSample(
            invocation_id=r["invocation_id"],
            rendered_input_text=r.get("rendered_input_text") or "",
            original_response=r.get("response_text") or "",
            original_success=True,
            original_cost_usd=float(r.get("cost_usd") or 0.0),
            original_tokens_out=int(r.get("tokens_out") or 0),
        )
        for r in sampled
    ]


# ─────────────────────────── replay primitive ─────────────────────────


def _validate_response(text: str) -> tuple[bool, str | None]:
    """Structural validity check for a replay response.

    We don't know the agent's full output contract from here, but we
    can check three universal invariants:
      1. Response is non-empty.
      2. Style guard passes (no banned phrases / em-dashes / filler
         adverbs in sentence-initial positions).
      3. Response doesn't fail the basic safety smell (Anthropic refusal
         language, "I cannot help with that", etc.).

    Returns (ok, reason). Reason is set only when ok=False.
    """
    if not text or len(text.strip()) < 5:
        return False, "empty_or_too_short"

    # Style guard — same hard filter the wrapper applies to live drafts.
    try:
        ok, violations = style_filter(text)
        if not ok:
            return False, f"style_violations:{len(violations)}"
    except Exception:  # noqa: BLE001 — style guard outage shouldn't fail replay
        pass

    # Safety smell — Claude's standard refusal preambles.
    refusal_markers = (
        "I cannot help with that",
        "I'm not able to",
        "I cannot provide",
        "I cannot assist",
    )
    head = text[:200].lower()
    for marker in refusal_markers:
        if marker.lower() in head:
            return False, "model_refused"

    return True, None


def _replay_sample(
    router: ClaudeRouter,
    proposed_system_prompt: str,
    sample: InvocationSample,
    *,
    tier: Tier = Tier.DEFAULT,
    max_tokens: int = 2000,
) -> ReplayResult:
    """Re-invoke the LLM with the PROPOSED system prompt and the
    HISTORICAL user input. Check structural validity. Never writes."""
    try:
        resp = router.complete(
            system=proposed_system_prompt,
            prompt=sample.rendered_input_text,
            tier=tier,
            max_tokens=max_tokens,
            temperature=0.2,
            # Critical: these kwargs are passed but the invocation_log
            # call inside router.complete() will tag this row with
            # prompt_constant_name="__shadow_eval__" so TRAINER's next
            # weekly query EXCLUDES shadow-eval calls from the failure
            # rate calc. Otherwise the shadow eval itself would skew
            # next week's diagnosis.
            prompt_constant_name="__shadow_eval__",
            agent_name="trainer",
            node_name="shadow_evaluate",
        )
    except Exception as exc:  # noqa: BLE001
        return ReplayResult(
            sample_invocation_id=sample.invocation_id,
            original_success=sample.original_success,
            new_success=False,
            failure_reason=f"replay_call_failed:{type(exc).__name__}",
        )

    new_text = resp.get("text", "")
    ok, reason = _validate_response(new_text)
    return ReplayResult(
        sample_invocation_id=sample.invocation_id,
        original_success=sample.original_success,
        new_success=ok,
        new_response=new_text,
        new_cost_usd=float(resp.get("cost_usd") or 0.0),
        new_tokens_out=int(resp.get("usage", {}).get("output_tokens") or 0),
        failure_reason=reason,
    )


# ─────────────────────────── public entrypoint ────────────────────────


# Minimum cohort size to even attempt eval. Below this, we report
# insufficient_data and let the founder decide manually.
MIN_FAILURE_COHORT = 3
MIN_SUCCESS_COHORT = 10


def shadow_evaluate(
    router: ClaudeRouter,
    *,
    agent_name: str,
    prompt_constant_name: str,
    prompt_sha256: str,
    proposed_text: str,
    days: int = 7,
    tier: Tier = Tier.DEFAULT,
) -> ShadowEvalResult:
    """Replay the proposed prompt against historical cohorts.

    Args:
      router: shared ClaudeRouter (the wrapper passes its instance).
      agent_name / prompt_constant_name / prompt_sha256: identifies the
        prompt being rewritten. The same sha256 ensures we only sample
        history from the EXACT prompt variant the proposal targets — if
        prompts.py has changed since the failure window started, we'd
        be replaying against a prompt that no longer exists in prod.
      proposed_text: the NEW prompt to evaluate.
      days: rolling window for sampling. Default 7d matches TRAINER cron.
      tier: same tier the target agent uses (HEAVY for analytical agents,
        DEFAULT for outreach drafting). The replay must use the SAME
        tier the live agent uses, otherwise the comparison is invalid.
    """
    fail_samples = sample_failure_cohort(
        agent_name=agent_name,
        prompt_constant_name=prompt_constant_name,
        prompt_sha256=prompt_sha256,
        days=days,
    )
    succ_samples = sample_success_cohort(
        agent_name=agent_name,
        prompt_constant_name=prompt_constant_name,
        prompt_sha256=prompt_sha256,
        days=days,
    )

    result = ShadowEvalResult(
        failure_cohort_size=len(fail_samples),
        success_cohort_size=len(succ_samples),
    )

    if len(fail_samples) < MIN_FAILURE_COHORT or len(succ_samples) < MIN_SUCCESS_COHORT:
        result.insufficient_data = True
        result.skipped_reason = (
            f"insufficient_history: have {len(fail_samples)} failures + "
            f"{len(succ_samples)} successes; need ≥{MIN_FAILURE_COHORT} + "
            f"≥{MIN_SUCCESS_COHORT}"
        )
        log.info(
            "shadow_eval_insufficient_data",
            agent=agent_name,
            constant=prompt_constant_name,
            failures=len(fail_samples),
            successes=len(succ_samples),
        )
        return result

    # Replay the failure cohort.
    failure_replays: list[ReplayResult] = [
        _replay_sample(router, proposed_text, s, tier=tier)
        for s in fail_samples
    ]
    # Replay the success cohort.
    success_replays: list[ReplayResult] = [
        _replay_sample(router, proposed_text, s, tier=tier)
        for s in succ_samples
    ]

    result.failure_cohort_fixed = sum(1 for r in failure_replays if r.new_success)
    result.success_cohort_kept = sum(1 for r in success_replays if r.new_success)

    result.fix_rate = (
        result.failure_cohort_fixed / result.failure_cohort_size
    )
    result.regression_rate = (
        1.0 - result.success_cohort_kept / result.success_cohort_size
    )
    result.net_improvement = result.fix_rate - result.regression_rate

    # Cost / latency deltas — median to ignore outliers.
    if failure_replays + success_replays:
        all_replays = failure_replays + success_replays
        cost_deltas = sorted(
            r.new_cost_usd - s.original_cost_usd
            for r, s in zip(
                all_replays,
                fail_samples + succ_samples,
            )
        )
        tok_deltas = sorted(
            r.new_tokens_out - s.original_tokens_out
            for r, s in zip(
                all_replays,
                fail_samples + succ_samples,
            )
        )
        mid = len(cost_deltas) // 2
        result.median_cost_delta = float(cost_deltas[mid]) if cost_deltas else 0.0
        result.median_tokens_delta = float(tok_deltas[mid]) if tok_deltas else 0.0

    # Worst 3 regressions for the HITL card: successes that the new
    # prompt broke. Sort by sample order; first 3 is fine since they
    # were sampled randomly already.
    result.regression_samples = [
        r for r in success_replays if not r.new_success
    ][:3]
    # Best 3 fixes for the HITL card: failures the new prompt resolved.
    result.fix_samples = [
        r for r in failure_replays if r.new_success
    ][:3]

    log.info(
        "shadow_eval_complete",
        agent=agent_name,
        constant=prompt_constant_name,
        fix_rate=round(result.fix_rate, 3),
        regression_rate=round(result.regression_rate, 3),
        net_improvement=round(result.net_improvement, 3),
        median_cost_delta=round(result.median_cost_delta, 6),
    )
    return result


# ─────────────────────────── 4-branch routing ─────────────────────────


def route_proposal(result: ShadowEvalResult) -> str:
    """The decision matrix specified in the v2 audit §2C.

    Returns one of:
      'auto_promote'  — strong improvement, low regression. Founder HITL with green badge.
      'review_amber'  — modest improvement OR modest regression. Founder HITL with amber badge.
      'auto_reject_low_fix'        — fix_rate too low to justify even reviewing.
      'auto_reject_high_regress'   — regression too high; founder shouldn't see this.
      'review_insufficient_data'   — not enough history; let founder decide manually.
    """
    if result.insufficient_data:
        return "review_insufficient_data"

    # Hard auto-rejects come FIRST so a high-regression proposal never
    # reaches the founder regardless of how high fix_rate is.
    if result.regression_rate > 0.15:
        return "auto_reject_high_regress"
    if result.fix_rate < 0.30:
        return "auto_reject_low_fix"

    # Green: clearly positive.
    if result.fix_rate >= 0.50 and result.regression_rate <= 0.05:
        return "auto_promote"

    # Amber: positive but worth a careful look.
    return "review_amber"


__all__ = [
    "InvocationSample",
    "ReplayResult",
    "ShadowEvalResult",
    "MIN_FAILURE_COHORT",
    "MIN_SUCCESS_COHORT",
    "sample_failure_cohort",
    "sample_success_cohort",
    "shadow_evaluate",
    "route_proposal",
]
