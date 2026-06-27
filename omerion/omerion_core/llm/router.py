"""Claude router — tiered model selection.

Tier mapping:
  FAST    → Haiku   (classifications, extractions, short drafts)
  DEFAULT → Sonnet  (orchestration, mid-length drafts, reasoning)
  HEAVY   → Opus    (architecture, W5H extraction, multi-step reasoning)
"""
from __future__ import annotations

from enum import Enum
from typing import Any

import httpx
from anthropic import (
    Anthropic,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.langfuse_client import lf_generation

log = get_logger("omerion.llm.router")


class Tier(str, Enum):
    FAST = "fast"          # Haiku
    DEFAULT = "default"    # Sonnet
    HEAVY = "heavy"        # Opus


# Rough per-1M-token pricing for telemetry cost attribution
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6":           (15.00, 75.00),
    "claude-sonnet-4-6":         ( 3.00, 15.00),
    "claude-haiku-4-5-20251001": ( 1.00,  5.00),
}


def _model_for(tier: Tier) -> str:
    if tier == Tier.HEAVY:
        return settings.claude_model_opus
    if tier == Tier.FAST:
        return settings.claude_model_haiku
    return settings.claude_model_sonnet


def _cost(
    model: str,
    in_tok: int,
    out_tok: int,
    cache_read_tok: int = 0,
    cache_write_tok: int = 0,
) -> float:
    """Cost attribution accounting for cache reads (10% of base) and writes (125% of base).

    `in_tok` from the Anthropic response excludes cache-read and cache-write
    tokens, so we add their discounted/premium counterparts separately.
    """
    if model not in _PRICING:
        return 0.0
    pin, pout = _PRICING[model]
    base = (in_tok / 1_000_000) * pin + (out_tok / 1_000_000) * pout
    cache_read_cost = (cache_read_tok / 1_000_000) * pin * 0.10
    cache_write_cost = (cache_write_tok / 1_000_000) * pin * 1.25
    return base + cache_read_cost + cache_write_cost


def _estimate_max_cost(model: str, max_tokens: int, messages: list[dict] | None,
                       system: Any) -> float:
    """Worst-case cost upper bound for the *pre-call* budget gate.

    We don't know the actual input/output token counts until the response
    returns, so this is a conservative ceiling used only to decide whether
    to refuse the call. Recorded cost (post-call) uses real token usage.

    Estimation strategy:
      - Output: assume `max_tokens` (the upper bound caller has approved).
      - Input:  rough 4-chars-per-token heuristic over messages + system.
    """
    if model not in _PRICING:
        return 0.0
    pin, pout = _PRICING[model]

    in_chars = 0
    if isinstance(system, str):
        in_chars += len(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                content = block.get("text") or block.get("content") or ""
                if isinstance(content, str):
                    in_chars += len(content)
    for msg in (messages or []):
        content = msg.get("content")
        if isinstance(content, str):
            in_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or ""
                    if isinstance(text, str):
                        in_chars += len(text)
    est_in_tok = in_chars // 4
    return (est_in_tok / 1_000_000) * pin + (max_tokens / 1_000_000) * pout


class ClaudeRouter:
    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY must be set")
        # Explicit, bounded timeout. The SDK default is 600s, so a hung socket
        # would block a LangGraph node for 10 minutes inside the executor's
        # 30-min wall-clock budget. 120s read covers long completions (8k tokens
        # + extended thinking); 10s connect fails fast on a dead endpoint.
        self._client = Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        # Retry transient infra failures: rate limits (429), 5xx (APIStatusError),
        # AND network-layer errors (timeout / connection reset) which the prior
        # config silently let crash the calling node on the first failure.
        retry=retry_if_exception_type(
            (RateLimitError, APIStatusError, APITimeoutError, APIConnectionError)
        ),
    )
    def _complete(self, *, model: str, system: Any, messages: list[dict[str, Any]],
                  max_tokens: int, temperature: float, tools: list[dict] | None,
                  thinking: bool = False, thinking_budget: int = 10_000) -> Any:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if thinking:
            # Extended thinking requires temperature=1 per Anthropic API
            kwargs["temperature"] = 1.0
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }
        
        from omerion_core.rate_limit.token_bucket import BUCKETS
        BUCKETS["anthropic"].acquire()
        return self._client.messages.create(**kwargs)

    def complete(
        self,
        *,
        tier: Tier = Tier.DEFAULT,
        system: str | list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        prompt: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        tools: list[dict] | None = None,
        thinking: bool | None = None,
        thinking_budget: int = 10_000,
        # ── Wave 5 v2.1: TRAINER attribution kwargs (all optional) ──
        # Pass these from agent code so prompt_invocations rows can be
        # attributed to (agent, node, prompt_constant_name). Existing
        # 64 call sites that don't pass them still work — the row is
        # written with agent_name='unknown'. TRAINER filters those out.
        prompt_constant_name: str | None = None,
        agent_name: str | None = None,
        node_name: str | None = None,
        run_id: Any = None,
        correlation_id: Any = None,
        # ── Wave 6 v1: ClaudeRouter hardening (all opt-in, default OFF) ──
        # Tri-state: True=always filter, False=never filter, None=allowlist
        # decides (see runtime_config.HUMAN_FACING_DEFAULT_AGENTS).
        human_facing: bool | None = None,
        # Caller-supplied upper bound for the budget pre-check. When None,
        # we estimate from `max_tokens` + char-based input heuristic.
        estimated_max_cost_usd: float | None = None,
    ) -> dict[str, Any]:
        """Unified completion with tiered Claude.

        `system` accepts a plain string or a list of structured blocks. Use
        helpers in `omerion_core.llm.cache` to attach `cache_control` for
        prompt caching on long, stable system prompts.

        Wave 5 v2.1: pass `prompt_constant_name` + `agent_name` + `run_id`
        to enable TRAINER's per-prompt failure attribution. The result
        dict gains an `invocation_id` field that the wrapper uses to
        backfill the success/error_class fields after node post-validation.

        Wave 6 v1: pass `human_facing=True` to force `style_guard.filter()`
        on the output (when `settings.enable_router_style_filter` is on).
        Per-agent budgets in `runtime_config.AGENT_BUDGETS` are enforced
        when `settings.enable_agent_budgets` is on. If a call is blocked
        by budget, returns a result with `blocked=True` and `blocked_reason`
        without invoking Anthropic.

        Returns a normalized dict: {text, model, usage, cost_usd, cache_usage,
        provider, invocation_id} (+ blocked/blocked_reason on budget block,
        + style_violations on style block).
        """
        if messages is None:
            if prompt is None:
                raise ValueError("must provide either messages or prompt")
            messages = [{"role": "user", "content": prompt}]

        model = _model_for(tier)

        # Auto-enable extended thinking for Opus unless caller explicitly opted out
        use_thinking = thinking if thinking is not None else (tier == Tier.HEAVY)

        # ── Langfuse labels — propagate REAL agent/node, not "unknown" ───
        # Bug fix (Wave 6 v1): `_agent` was hardcoded "unknown" so every
        # trace in Langfuse appeared as `unknown.llm_call` even though
        # callers already passed `agent_name=` for log_invocation. Now the
        # trace is correctly attributed when the caller supplies it.
        _agent = agent_name or "unknown"
        _node = node_name or "llm_call"
        _sys_str = system if isinstance(system, str) else None
        _usr_str = messages[-1]["content"] if messages else None
        if isinstance(_usr_str, list):
            _usr_str = str(_usr_str)[:400]

        # ── 1. BUDGET PRE-CHECK (flagged, fast-fail before Anthropic) ───
        if settings.enable_agent_budgets and agent_name:
            from omerion_core.llm.runtime_config import budget_for
            from omerion_core.llm.budget_backend import budget_backend
            _budget = budget_for(agent_name)
            if _budget is not None:
                _run_key = str(run_id) if run_id else None
                _daily_spent, _run_spent = budget_backend.get_spend(agent_name, _run_key)
                _estimate = (
                    estimated_max_cost_usd
                    if estimated_max_cost_usd is not None
                    else _estimate_max_cost(model, max_tokens, messages, system)
                )
                if _daily_spent + _estimate > _budget.daily_usd:
                    log.warning(
                        "router_budget_blocked_daily",
                        agent=agent_name, run_id=str(run_id) if run_id else None,
                        spent=_daily_spent, estimate=_estimate, cap=_budget.daily_usd,
                    )
                    return _build_blocked_result(
                        model=model, reason="daily_cap",
                        spent=_daily_spent, estimate=_estimate, cap=_budget.daily_usd,
                    )
                if _budget.per_run_usd > 0 and _run_spent + _estimate > _budget.per_run_usd:
                    log.warning(
                        "router_budget_blocked_per_run",
                        agent=agent_name, run_id=str(run_id) if run_id else None,
                        spent=_run_spent, estimate=_estimate, cap=_budget.per_run_usd,
                    )
                    return _build_blocked_result(
                        model=model, reason="per_run_cap",
                        spent=_run_spent, estimate=_estimate, cap=_budget.per_run_usd,
                    )

        # ── 2. ANTHROPIC CALL with correctly-labelled Langfuse trace ────
        with lf_generation(
            agent=_agent,
            node=_node,
            model=model,
            system=_sys_str,
            user=_usr_str,
            session_id=str(run_id) if run_id else None,
        ) as _lf_gen:
            resp = self._complete(
                model=model, system=system, messages=messages,
                max_tokens=max_tokens, temperature=temperature, tools=tools,
                thinking=use_thinking, thinking_budget=thinking_budget,
            )
            text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            thinking_parts = [b.thinking for b in resp.content if getattr(b, "type", None) == "thinking"]
            usage = resp.usage
            # Cache-aware fields are present only when cache_control was used.
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cost = _cost(model, usage.input_tokens, usage.output_tokens, cache_read, cache_write)

            # Emit to Langfuse (no-op if disabled). Metadata now includes
            # agent+node so traces are filterable/groupable in the dashboard.
            _lf_gen.end(
                output=("\n".join(text_parts))[:400],
                usage={
                    "input": usage.input_tokens,
                    "output": usage.output_tokens,
                    "total": usage.input_tokens + usage.output_tokens,
                    "unit": "TOKENS",
                },
                metadata={
                    "cost_usd": cost,
                    "cache_read": cache_read,
                    "cache_write": cache_write,
                    "thinking_enabled": use_thinking,
                    "agent": _agent,
                    "node": _node,
                    "run_id": str(run_id) if run_id else None,
                },
            )

        # ── 3. RECORD ACTUAL SPEND (flagged, after Anthropic returns) ───
        if settings.enable_agent_budgets and agent_name and cost > 0:
            try:
                from omerion_core.llm.budget_backend import budget_backend
                budget_backend.add_spend(
                    agent_name, str(run_id) if run_id else None, cost
                )
            except Exception as exc:  # noqa: BLE001 — never break the caller
                log.warning("router_budget_record_failed",
                            agent=agent_name, error=str(exc))

        result: dict[str, Any] = {
            "text": "\n".join(text_parts),
            "raw": resp,
            "model": model,
            "provider": "anthropic",
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
            },
            "cost_usd": cost,
        }
        if thinking_parts:
            result["thinking"] = "\n".join(thinking_parts)

        # ── Wave 5 v2.1: log the invocation for TRAINER attribution ──
        # Best-effort — never raises. Lazy import keeps the router free
        # of a Supabase dependency at module load time.
        invocation_id = None
        try:
            from omerion_core.telemetry.invocation_log import log_invocation
            invocation_id = log_invocation(
                agent_name=agent_name,
                node_name=node_name,
                prompt_constant_name=prompt_constant_name,
                system_text=_sys_str,
                user_text=_usr_str if isinstance(_usr_str, str) else None,
                response_text=result["text"],
                model=model,
                tier=tier.value if hasattr(tier, "value") else str(tier),
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                cost_usd=cost,
                run_id=run_id,
                correlation_id=correlation_id,
            )
        except Exception:  # noqa: BLE001 — never block the LLM call
            pass

        if invocation_id is not None:
            result["invocation_id"] = str(invocation_id)

        # ── 4. STYLE FILTER (flagged + tri-state human_facing) ──────────
        # SOFT-fail: we add `style_violations` + `blocked=True` to the result
        # but do not raise. The caller (already wired for HITL) decides
        # whether to re-prompt, route to founder review, or surface an
        # error. Raising here would silently break the 42 existing call
        # sites whose contracts don't know about this behaviour.
        if settings.enable_router_style_filter:
            from omerion_core.llm.runtime_config import is_human_facing
            if is_human_facing(agent_name, human_facing):
                try:
                    from omerion_core.outreach.style_guard import filter as style_filter
                    ok, violations = style_filter(result["text"])
                    if not ok:
                        result["style_violations"] = violations
                        result["blocked"] = True
                        result["blocked_reason"] = "style_filter"
                        log.warning(
                            "router_style_blocked",
                            agent=agent_name, node=node_name,
                            violation_count=len(violations),
                            sample=violations[:3],
                        )
                except Exception as exc:  # noqa: BLE001 — filter must not break the call
                    log.warning("router_style_filter_failed",
                                agent=agent_name, error=str(exc))

        return result


# ── Blocked-result helper (budget) ────────────────────────────────────────────

def _build_blocked_result(*, model: str, reason: str, spent: float,
                          estimate: float, cap: float) -> dict[str, Any]:
    """Shape a router-blocked result. Same top-level keys as a real result so
    callers using `result["text"]` get an empty string instead of KeyError,
    plus explicit `blocked=True` so they can branch.
    """
    return {
        "text": "",
        "raw": None,
        "model": model,
        "provider": "anthropic",
        "usage": {"input_tokens": 0, "output_tokens": 0,
                  "cache_read_tokens": 0, "cache_write_tokens": 0},
        "cost_usd": 0.0,
        "blocked": True,
        "blocked_reason": reason,           # "daily_cap" | "per_run_cap"
        "budget_spent_usd": spent,
        "budget_estimate_usd": estimate,
        "budget_cap_usd": cap,
    }


# Module-level singleton for convenience
_router: ClaudeRouter | None = None


def claude() -> ClaudeRouter:
    global _router
    if _router is None:
        _router = ClaudeRouter()
    return _router
