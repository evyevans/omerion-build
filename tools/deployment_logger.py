"""
Deployment logging decorator — wraps every AI tool call and writes a row
to the Supabase agent_runs table with timing, token counts, cost, and status.

REWIRED: Uses Supabase directly instead of Google Sheets append_row.
"""
import functools
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.deployment_logger")

_MODEL_PRICING = {
    "claude-sonnet-4-6":  {"input": 3.00, "output": 15.00},
    "claude-opus-4-6":    {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5":   {"input": 0.80, "output": 4.00},
}


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _MODEL_PRICING.get(model, {"input": 3.00, "output": 15.00})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def _write_deployment(row: dict) -> None:
    """Write deployment record to Supabase agent_runs table."""
    try:
        supabase.table("agent_runs").insert(row).execute()
    except Exception as exc:
        # Never let logging failure kill the main function
        log.warning("deployment_log_write_error", error=str(exc))


def log_deployment(
    skill_name: str,
    triggered_by: str = "manual",
    model: str = "claude-sonnet-4-6",
):
    """Decorator factory for AI execution logging.

    Usage:
        @log_deployment(skill_name="scout_classify", triggered_by="cron")
        def my_fn(contact_data):
            response = anthropic_client.messages.create(...)
            # attach token counts so the decorator can read them:
            my_fn._last_usage = response.usage
            return result
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Any:
            run_id = str(uuid4())
            started_at = datetime.now(timezone.utc).isoformat()
            t0 = time.perf_counter()
            status = "completed"
            error_message = ""
            input_tokens = 0
            output_tokens = 0
            result = None

            try:
                result = fn(*args, **kwargs)
                usage = getattr(fn, "_last_usage", None)
                if usage:
                    input_tokens = getattr(usage, "input_tokens", 0)
                    output_tokens = getattr(usage, "output_tokens", 0)
            except Exception as exc:
                status = "failed"
                error_message = traceback.format_exc()[-500:]
                raise
            finally:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                completed_at = datetime.now(timezone.utc).isoformat()
                cost_usd = _calc_cost(model, input_tokens, output_tokens)

                row = {
                    "run_id": run_id,
                    "agent_name": skill_name,
                    "status": status,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "duration_ms": duration_ms,
                    "llm_model": model,
                    "llm_input_tokens": input_tokens,
                    "llm_output_tokens": output_tokens,
                    "llm_cost_usd": round(cost_usd, 6),
                    "triggered_by": triggered_by,
                    "error_log": error_message if error_message else None,
                    "created_at": started_at,
                }
                _write_deployment(row)

            return result
        return wrapper
    return decorator
