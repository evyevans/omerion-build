"""FastAPI app — canonical Omerion runtime (root core/ deprecated, see plan).

Serves:
    POST /hitl/resolve          — Discord bot approve/reject/edit
    GET  /hitl/pending          — list pending approvals
    POST /webhooks/fireflies    — Fireflies transcript.completed
    POST /webhooks/stripe       — Stripe billing events (source-of-truth for revenue)
    POST /webhooks/base44/intake — Agentic Factory self-serve form submission
    GET  /diagrams/{id}         — Serve generated HTML blueprint files
    GET  /health                — liveness + config check
    GET  /health/services       — per-service connectivity
    POST /inbound/discord/route — Discord bot message routing (channel → skill)
    POST /inbound/hitl/approve  — Discord APPROVE button adapter
    POST /inbound/hitl/reject   — Discord REJECT button adapter
    POST /agents/{name}/run     — control-plane manual agent invocation
    GET  /agents/runs/{id}      — run inspection
    GET  /mission-control       — Mission Control dashboard feed
    GET  /reports/*             — daily / status / attribution / pipeline / costs / R&D

Reserved slots: /webhooks/calendly. Each follows the same
signature-verified-or-bearer pattern.
"""
from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from omerion_core.inbound.discord_route import router as discord_router
from omerion_core.inbound.fireflies import router as fireflies_router
from omerion_core.inbound.hitl import router as hitl_router
from omerion_core.inbound.knowledge_base import knowledge_base_router
from omerion_core.inbound.routes.control_plane import router as control_plane_router
from omerion_core.inbound.routes.health import router as health_services_router
from omerion_core.inbound.signatures import require_bearer
from omerion_core.inbound.stripe import router as stripe_router
from omerion_core.inbound.internal_bridges import router as bridge_router
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.inbound.discord_hitl")

try:
    from omerion_core.inbound.discord_voice import router as discord_voice_router
except ImportError:
    discord_voice_router = None

try:
    from omerion_core.inbound.base44_intake import router as base44_intake_router
except ImportError:
    base44_intake_router = None

try:
    from omerion_core.inbound.diagram_server import router as diagram_server_router
except ImportError:
    diagram_server_router = None

app = FastAPI(
    title="Omerion Local Runtime",
    version="1.0.0",
    docs_url="/_docs" if settings.omerion_env != "prod" else None,
)

# In dev: wildcard so the local dashboard (localhost:3000) and any test client work freely.
# In prod: restrict to the real origins that legitimately call these endpoints.
_PROD_ORIGINS = [
    "https://omerion.io",
    "https://www.omerion.io",
    "https://app.base44.com",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.omerion_env != "prod" else _PROD_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS", "PATCH", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.include_router(hitl_router)
app.include_router(fireflies_router)
app.include_router(health_services_router)
app.include_router(control_plane_router)
app.include_router(discord_router)
if discord_voice_router is not None:
    app.include_router(discord_voice_router)
else:
    log.warning("discord_voice_router_missing")
app.include_router(knowledge_base_router)
app.include_router(stripe_router)
# github_webhook_router removed 2026-06-21 — its only job was triggering the
# validator agent, which migrated to the Claude Dev cloud platform (PR review
# now runs there via the validator skill + GitHub MCP).
if base44_intake_router is not None:
    app.include_router(base44_intake_router)   # Agentic Factory self-serve intake
else:
    log.warning("base44_intake_router_missing")
if diagram_server_router is not None:
    app.include_router(diagram_server_router)  # Serve generated HTML blueprints
else:
    log.warning("diagram_server_router_missing")
app.include_router(bridge_router)          # Hermes VPS + managed-agent webhook bridges


# ── Discord HITL button adapters ────────────────────────────────────────────────
# The Discord bot POSTs to these paths when the founder clicks
# ✅ APPROVE or ❌ REJECT inside #founder-hitl.
# They translate the Discord payload into the existing /hitl/resolve format.


def _resume_after_decision(session_id: str, background_tasks: BackgroundTasks) -> None:
    """Schedule execute_resume if all reviews for session_id are now decided."""
    from omerion_core.hitl.review import get_all_reviews_by_session, get_pending_count_by_session
    from omerion_core.runtime import run_lifecycle
    from omerion_core.runtime.run_executor import execute_resume

    if get_pending_count_by_session(session_id) > 0:
        return  # more approvals still outstanding
    all_reviews = get_all_reviews_by_session(session_id)
    decisions = {r["review_id"]: r["decision"] for r in all_reviews}
    try:
        run_lifecycle.transition(session_id, "running")
    except Exception as exc:  # noqa: BLE001
        log.warning("discord_hitl_transition_failed", session_id=session_id, error=str(exc))
    background_tasks.add_task(execute_resume, session_id, {"decisions": decisions})
    log.info("discord_hitl_graph_queued", session_id=session_id)


# ── Pydantic models for the Discord HITL adapter payloads ─────────────
#
# Wave 1.4: every webhook input is schema-validated. Raw `await request.json()`
# is banned because (a) it lets malformed bodies through to business logic,
# and (b) it's impossible to test the contract without an HTTP client.


class DiscordHitlButtonPayload(BaseModel):
    """Payload posted by the Discord bot when the founder clicks the
    ✅ APPROVE or ❌ REJECT button on a HITL review card."""

    session_id: str = Field(min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=2000)


class DiscordHitlAdapterResponse(BaseModel):
    ok: bool
    session_id: str = ""
    decision: str = ""
    error: str = ""


@app.post(
    "/inbound/hitl/approve",
    tags=["hitl"],
    dependencies=[Depends(require_bearer)],
    response_model=DiscordHitlAdapterResponse,
)
async def discord_hitl_approve(
    body: DiscordHitlButtonPayload,
    background_tasks: BackgroundTasks,
) -> DiscordHitlAdapterResponse:
    """Adapter: Discord APPROVE button → resolve review + resume LangGraph if last pending."""
    from omerion_core.hitl.review import get_review_by_session, resolve_review

    session_id = body.session_id
    log.info("discord_hitl_approve", session_id=session_id)
    try:
        review = get_review_by_session(session_id)
        if not review:
            log.warning("discord_hitl_approve_no_review", session_id=session_id)
            return DiscordHitlAdapterResponse(
                ok=False,
                session_id=session_id,
                error=f"No pending review found for session_id={session_id}",
            )
        resolve_review(
            review["review_id"],
            token=review["approve_token"],
            decision="approved",
            notes=body.notes or "Approved via Discord button",
        )
        log.info("discord_hitl_approved", session_id=session_id, review_id=review["review_id"])
        _resume_after_decision(session_id, background_tasks)
        return DiscordHitlAdapterResponse(ok=True, session_id=session_id, decision="approved")
    except Exception as exc:  # noqa: BLE001
        log.error("discord_hitl_approve_error", session_id=session_id, error=str(exc))
        return DiscordHitlAdapterResponse(ok=False, session_id=session_id, error=str(exc))


@app.post(
    "/inbound/hitl/reject",
    tags=["hitl"],
    dependencies=[Depends(require_bearer)],
    response_model=DiscordHitlAdapterResponse,
)
async def discord_hitl_reject(
    body: DiscordHitlButtonPayload,
    background_tasks: BackgroundTasks,
) -> DiscordHitlAdapterResponse:
    """Adapter: Discord REJECT button → resolve review + resume LangGraph if last pending."""
    from omerion_core.hitl.review import get_review_by_session, resolve_review

    session_id = body.session_id
    log.info("discord_hitl_reject", session_id=session_id)
    try:
        review = get_review_by_session(session_id)
        if not review:
            log.warning("discord_hitl_reject_no_review", session_id=session_id)
            return DiscordHitlAdapterResponse(
                ok=False,
                session_id=session_id,
                error=f"No pending review found for session_id={session_id}",
            )
        resolve_review(
            review["review_id"],
            token=review["reject_token"],
            decision="rejected",
            notes=body.notes or "Rejected via Discord button",
        )
        log.info("discord_hitl_rejected", session_id=session_id, review_id=review["review_id"])
        _resume_after_decision(session_id, background_tasks)
        return DiscordHitlAdapterResponse(ok=True, session_id=session_id, decision="rejected")
    except Exception as exc:  # noqa: BLE001
        log.error("discord_hitl_reject_error", session_id=session_id, error=str(exc))
        return DiscordHitlAdapterResponse(ok=False, session_id=session_id, error=str(exc))


def _deep_health() -> tuple[dict[str, str], int]:
    """Wave 3.6: deep healthcheck — checks broker, scheduler, DB.

    Returns (payload, status_code). 503 if any critical dependency is
    unreachable. Railway uses this to detect a hung container and
    auto-restart it. Cheap enough to call on every request.
    """
    status_code = 200
    payload: dict[str, str] = {
        "status": "ok",
        "env": settings.omerion_env,
        "webhook_token_configured": "yes" if settings.omerion_webhook_token else "no",
        "fireflies_secret_configured": "yes" if settings.fireflies_webhook_secret else "no",
        "stripe_secret_configured": "yes" if settings.stripe_webhook_secret else "no",
        "database_url_configured": "yes" if settings.database_url else "no",
    }

    # DB reachability — cheap SELECT 1-shaped query via Supabase REST.
    try:
        from omerion_core.clients.supabase_client import supabase

        supabase.table("agent_runs").select("run_id").limit(1).execute()
        payload["db"] = "up"
    except Exception as exc:  # noqa: BLE001
        payload["db"] = f"down: {exc.__class__.__name__}"
        payload["status"] = "degraded"
        status_code = 503

    # APScheduler liveness — check the global from omerion/main.py lifespan.
    try:
        import sys
        main_mod = sys.modules.get("main") or sys.modules.get("omerion.main")
        sched = getattr(main_mod, "_scheduler", None) if main_mod else None
        if sched is not None and getattr(sched, "running", False):
            payload["scheduler"] = "up"
        else:
            payload["scheduler"] = "down"
            payload["status"] = "degraded"
            status_code = 503
    except Exception as exc:  # noqa: BLE001
        payload["scheduler"] = f"unknown: {exc.__class__.__name__}"

    # Event broker liveness — channel is registered as _broker_channel.
    try:
        import sys
        main_mod = sys.modules.get("main") or sys.modules.get("omerion.main")
        chan = getattr(main_mod, "_broker_channel", None) if main_mod else None
        payload["broker"] = "up" if chan is not None else "down"
        if chan is None:
            payload["status"] = "degraded"
            status_code = 503
    except Exception as exc:  # noqa: BLE001
        payload["broker"] = f"unknown: {exc.__class__.__name__}"

    return payload, status_code


@app.get("/health", tags=["health"])
def health() -> Any:
    from fastapi.responses import JSONResponse

    payload, status_code = _deep_health()
    return JSONResponse(payload, status_code=status_code)


# Compatibility alias: the legacy root app used /api/v1/health as the
# Railway healthcheck path. Keep it so railway.toml's healthcheckPath
# continues to resolve after the deploy-config switch lands.
@app.get("/api/v1/health", tags=["health"])
def health_v1_alias() -> Any:
    return health()
