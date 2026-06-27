"""Discord bot control-plane routes.

Narrow, explicit surface the Discord bot slash commands call into. Every
route is bearer-auth'd via the same `OMERION_WEBHOOK_TOKEN` the Discord bot
uses — stored in discord/.env.

Routes:
  POST /agents/{name}/run        — trigger an agent by skill name
  GET  /reports/daily            — compact daily digest (for 08:00/18:00)
  GET  /reports/status           — quick-glance agent performance rollup
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from omerion_core.clients.supabase_client import supabase
from omerion_core.inbound.signatures import require_bearer
from omerion_core.logging import get_logger

log = get_logger("omerion.inbound.control_plane")

router = APIRouter(tags=["control_plane"], dependencies=[Depends(require_bearer)])


# ─── Agent run trigger ────────────────────────────────────────────────

class AgentRunBody(BaseModel):
    inputs: dict[str, Any] | None = None
    source_channel: str = "discord"
    triggered_by: str | None = None
    discord_channel_id: str | None = None
    discord_thread_id: str | None = None


class AgentRunResponse(BaseModel):
    agent_name: str
    started: bool
    run_id: str | None = None
    session_id: str | None = None  # back-compat: equals run_id
    status: str | None = None
    note: str | None = None


@router.post("/agents/{name}/run", response_model=AgentRunResponse)
def run_agent(
    name: str,
    body: AgentRunBody,
    background_tasks: BackgroundTasks,
) -> AgentRunResponse:
    """Queue an agent run by skill name and return immediately with a run_id.

    The registry is the single source of truth for which name resolves to
    which LangGraph or Agent-SDK entrypoint — it's built from
    `skills/*.skill.md` frontmatter at boot. Execution itself runs in a
    BackgroundTasks worker via `run_executor.execute_run`, so the HTTP ack
    returns in <1s regardless of how long the graph takes (avoids Discord
    interaction timeout).
    """
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.registry import get_handler
        from omerion_core.runtime.run_executor import execute_run
    except ImportError as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"registry unavailable: {exc}") from exc

    try:
        get_handler(name)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown agent: {name}") from exc

    try:
        run = run_lifecycle.create_run(
            agent_name=name,
            source_channel=body.source_channel,
            inputs=body.inputs or {},
            triggered_by=body.triggered_by,
            discord_channel_id=body.discord_channel_id,
            discord_thread_id=body.discord_thread_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("agent_run_create_failed", agent=name, error=str(exc))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    background_tasks.add_task(execute_run, run["run_id"])
    log.info("agent_run_queued", agent=name, run_id=run["run_id"], source=body.source_channel)

    return AgentRunResponse(
        agent_name=name,
        started=True,
        run_id=run["run_id"],
        session_id=run["run_id"],
        status="queued",
    )


# ─── LinkedIn queue drain ─────────────────────────────────────────────

class DrainQueueBody(BaseModel):
    limit: int = 10
    timeout_per_message: float = 60.0


class DrainQueueResponse(BaseModel):
    sent: int
    blocked: int
    failed: int
    total: int


@router.post("/agents/linkedin_outreach/drain-queue", response_model=DrainQueueResponse)
def drain_linkedin_queue(body: DrainQueueBody, background_tasks: BackgroundTasks) -> DrainQueueResponse:
    """Trigger the browser-use sender to drain queued LinkedIn DMs and connection requests.

    Runs `linkedin_outreach.tools.send_queued_messages` in a background thread.
    Returns immediately with a summary — the actual sends happen asynchronously.
    """
    try:
        from agents.linkedin_outreach.tools import send_queued_messages  # type: ignore[import]
    except ImportError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"linkedin_outreach not importable: {exc}") from exc

    import threading
    result_holder: dict = {}

    def _run() -> None:
        result_holder.update(send_queued_messages(limit=body.limit, timeout_per_message=body.timeout_per_message))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=body.timeout_per_message * body.limit + 30)

    return DrainQueueResponse(
        sent=result_holder.get("sent", 0),
        blocked=result_holder.get("blocked", 0),
        failed=result_holder.get("failed", 0),
        total=result_holder.get("total", 0),
    )


# ─── Run lifecycle queries ─────────────────────────────────────────────

class AgentRunRow(BaseModel):
    run_id: str
    agent_name: str
    status: str
    source_channel: str
    triggered_by: str | None = None
    review_id: str | None = None
    correlation_id: str | None = None
    result_summary: str | None = None
    error: str | None = None
    cost_usd: float | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


@router.get("/agents/runs/{run_id}", response_model=AgentRunRow)
def get_agent_run(run_id: str) -> AgentRunRow:
    """Fetch the lifecycle row for a single run by id."""
    from omerion_core.runtime import run_lifecycle
    row = run_lifecycle.get_run(run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run not found: {run_id}")
    return AgentRunRow(**{k: row.get(k) for k in AgentRunRow.model_fields})


@router.get("/agents/runs", response_model=list[AgentRunRow])
def list_agent_runs(
    status_filter: str | None = None,
    agent_name: str | None = None,
    limit: int = 50,
) -> list[AgentRunRow]:
    """List runs, newest first. Filter by status and/or agent_name.

    Query param is `status_filter` (not `status`) to avoid colliding with
    the imported `fastapi.status` module in this file.
    """
    from omerion_core.runtime import run_lifecycle
    rows = run_lifecycle.list_runs(
        status=status_filter,
        agent_name=agent_name,
        limit=max(1, min(limit, 200)),
    )
    return [AgentRunRow(**{k: r.get(k) for k in AgentRunRow.model_fields}) for r in rows]


# ─── Mission Control (3-question dashboard) ───────────────────────────

class MissionControl(BaseModel):
    outcomes_today: int
    errors_today: int
    cost_usd_today: float
    completed_today: int
    in_flight_now: int
    hitl_waiting_now: int


@router.get("/mission-control", response_model=MissionControl)
def mission_control() -> MissionControl:
    """Today's outcomes / errors / cost — the three numbers Elon demanded."""
    from omerion_core.runtime.mission_control import snapshot
    return MissionControl(**snapshot())


# ─── Daily digest ─────────────────────────────────────────────────────

class DailyReport(BaseModel):
    run_date: date
    pending_reviews: int
    runs_last_24h: int
    new_accounts_24h: int
    new_opportunities_24h: int
    r4_alerts_24h: int
    headline: str


def _count(table: str, *filters) -> int:
    q = supabase.table(table).select("*", count="exact", head=True)
    for col, op, val in filters:
        q = getattr(q, op)(col, val)
    try:
        return q.execute().count or 0
    except Exception:  # noqa: BLE001
        return 0


@router.get("/reports/daily", response_model=DailyReport)
def daily_report() -> DailyReport:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    pending = _count("founder_review_queue", ("decision", "eq", "pending"))
    runs = _count("agent_runs", ("started_at", "gte", since))
    accounts = _count("accounts", ("created_at", "gte", since))
    opps = _count("opportunities", ("created_at", "gte", since))
    alerts = _count("r4_alerts", ("created_at", "gte", since))

    if pending == 0 and runs == 0:
        headline = "All quiet — no runs, no approvals waiting."
    elif pending > 0:
        headline = f"{pending} approval(s) waiting. {runs} agent run(s) in last 24h."
    else:
        headline = f"{runs} agent run(s) in last 24h. {opps} new opportunity(ies)."

    return DailyReport(
        run_date=date.today(),
        pending_reviews=pending,
        runs_last_24h=runs,
        new_accounts_24h=accounts,
        new_opportunities_24h=opps,
        r4_alerts_24h=alerts,
        headline=headline,
    )


# ─── Status rollup ────────────────────────────────────────────────────

class StatusRollupAgent(BaseModel):
    agent_name: str
    runs_14d: int
    success_rate: float
    latency_p95_ms: int
    cost_per_run_usd: float


class StatusRollup(BaseModel):
    as_of: datetime
    agents: list[StatusRollupAgent]


@router.get("/reports/status", response_model=StatusRollup)
def status_rollup() -> StatusRollup:
    try:
        resp = (
            supabase.table("agent_performance_metrics")
            .select("agent_name,runs_14d,success_rate,latency_p95_ms,cost_per_run_usd")
            .order("agent_name", desc=False)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("status_rollup_query_failed", error=str(exc))
        rows = []

    agents = [
        StatusRollupAgent(
            agent_name=r.get("agent_name", "unknown"),
            runs_14d=int(r.get("runs_14d") or 0),
            success_rate=float(r.get("success_rate") or 0.0),
            latency_p95_ms=int(r.get("latency_p95_ms") or 0),
            cost_per_run_usd=float(r.get("cost_per_run_usd") or 0.0),
        )
        for r in rows
    ]
    return StatusRollup(as_of=datetime.now(timezone.utc), agents=agents)


# ─── Agent pause / resume / enable ───────────────────────────────────

_MANAGED_AGENTS = {"r1_market_tech_watcher", "r2_oss_scout", "r3_strategic_architect", "r4_evaluation_telemetry"}


class AgentScheduleResponse(BaseModel):
    agent_name: str
    paused: bool
    note: str | None = None


def _scheduler_action(name: str, action: str) -> AgentScheduleResponse:
    if name in _MANAGED_AGENTS:
        return AgentScheduleResponse(
            agent_name=name,
            paused=(action == "pause"),
            note="managed agent — schedule owned by Anthropic cloud runtime, not local APScheduler",
        )
    try:
        from omerion_core.runtime.scheduler import get_scheduler
        sched = get_scheduler()
        job = sched.get_job(name)
        if job is None:
            return AgentScheduleResponse(agent_name=name, paused=False, note=f"no scheduled job found for '{name}'")
        if action == "pause":
            job.pause()
        else:
            job.resume()
        return AgentScheduleResponse(agent_name=name, paused=(action == "pause"))
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduler_action_failed", agent=name, action=action, error=str(exc))
        return AgentScheduleResponse(agent_name=name, paused=False, note=f"scheduler error: {exc}")


@router.post("/agents/{name}/pause", response_model=AgentScheduleResponse)
def pause_agent(name: str) -> AgentScheduleResponse:
    """Pause the APScheduler cron job for a LangGraph agent."""
    return _scheduler_action(name, "pause")


@router.post("/agents/{name}/resume", response_model=AgentScheduleResponse)
def resume_agent(name: str) -> AgentScheduleResponse:
    """Resume a paused APScheduler cron job."""
    return _scheduler_action(name, "resume")


@router.post("/agents/{name}/enable", response_model=AgentScheduleResponse)
def enable_agent(name: str) -> AgentScheduleResponse:
    """Alias for resume — used after GUARD auto-pause."""
    return _scheduler_action(name, "resume")


# ─── Cancel running session ───────────────────────────────────────────

class CancelResponse(BaseModel):
    session_id: str
    cancelled: bool
    note: str | None = None


@router.post("/agents/sessions/{session_id}/cancel", response_model=CancelResponse)
async def cancel_session(session_id: str) -> CancelResponse:
    """Cancel a running LangGraph thread by session_id."""
    try:
        from omerion_core.runtime.checkpointer import cancel_thread
        cancelled = await cancel_thread(session_id)
        if not cancelled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no active session: {session_id}")
        return CancelResponse(session_id=session_id, cancelled=True)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("cancel_session_failed", session_id=session_id, error=str(exc))
        return CancelResponse(session_id=session_id, cancelled=False, note=str(exc))


# ─── R&D Proposals ───────────────────────────────────────────────────

class ProposalSummary(BaseModel):
    proposal_id: str
    title: str
    target_module: str
    impact: str
    effort: str
    status: str


class ProposalDetail(BaseModel):
    proposal_id: str
    title: str
    problem_statement: str
    hypothesis: str
    design_doc_md: str
    target_module: str
    impact: str
    effort: str
    status: str
    blueprint_handoff: dict | None = None
    supporting_insight_ids: list[str] = []
    supporting_oss_ids: list[str] = []
    created_at: str | None = None


@router.get("/rd/proposals", response_model=list[ProposalSummary])
def list_proposals(status: str | None = None) -> list[ProposalSummary]:
    """List R3 proposals, optionally filtered by status."""
    q = supabase.table("rd_proposals").select(
        "proposal_id,title,target_module,impact,effort,status"
    ).order("created_at", desc=True).limit(20)
    if status:
        q = q.eq("status", status)
    try:
        rows = q.execute().data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("list_proposals_failed", error=str(exc))
        rows = []
    return [ProposalSummary(**{k: (r.get(k) or "") for k in ProposalSummary.model_fields}) for r in rows]


@router.get("/rd/proposals/{proposal_id}", response_model=ProposalDetail)
def get_proposal(proposal_id: str) -> ProposalDetail:
    """Fetch a single R3 proposal by ID."""
    try:
        rows = supabase.table("rd_proposals").select("*").eq("proposal_id", proposal_id).limit(1).execute().data or []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"proposal {proposal_id} not found")
    r = rows[0]
    return ProposalDetail(
        proposal_id=str(r.get("proposal_id", "")),
        title=r.get("title", ""),
        problem_statement=r.get("problem_statement", ""),
        hypothesis=r.get("hypothesis", ""),
        design_doc_md=r.get("design_doc_md", ""),
        target_module=r.get("target_module", ""),
        impact=r.get("impact", ""),
        effort=r.get("effort", ""),
        status=r.get("status", ""),
        blueprint_handoff=r.get("blueprint_handoff"),
        supporting_insight_ids=r.get("supporting_insight_ids") or [],
        supporting_oss_ids=r.get("supporting_oss_ids") or [],
        created_at=str(r.get("created_at", "")),
    )


# ─── Blueprint detail ─────────────────────────────────────────────────

class BlueprintDetail(BaseModel):
    blueprint_id: str
    persona: str | None = None
    service_package: str | None = None
    status: str | None = None
    w5h: dict | None = None
    ttwa: dict | None = None
    proposal: dict | None = None
    created_at: str | None = None


@router.get("/blueprints/{blueprint_id}", response_model=BlueprintDetail)
def get_blueprint(blueprint_id: str) -> BlueprintDetail:
    """Fetch a blueprint by ID — called by the Discord bot before deploy approval."""
    try:
        rows = supabase.table("blueprints").select("*").eq("blueprint_id", blueprint_id).limit(1).execute().data or []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"blueprint {blueprint_id} not found")
    r = rows[0]
    return BlueprintDetail(
        blueprint_id=str(r.get("blueprint_id", "")),
        persona=r.get("persona"),
        service_package=r.get("service_package"),
        status=r.get("status"),
        w5h=r.get("w5h"),
        ttwa=r.get("ttwa"),
        proposal=r.get("proposal"),
        created_at=str(r.get("created_at", "")),
    )


# ─── Attribution reports ──────────────────────────────────────────────

class AttributionReport(BaseModel):
    deployment_id: str
    client_slug: str | None = None
    persona: str | None = None
    service_package: str | None = None
    summary_md: str | None = None
    confidence: str | None = None
    case_study_triggered: bool = False
    created_at: str | None = None


def _attribution_row(r: dict) -> AttributionReport:
    return AttributionReport(
        deployment_id=str(r.get("deployment_id", "")),
        client_slug=r.get("client_slug"),
        persona=r.get("persona"),
        service_package=r.get("service_package"),
        summary_md=r.get("summary_md"),
        confidence=r.get("confidence"),
        case_study_triggered=bool(r.get("case_study_triggered")),
        created_at=str(r.get("created_at", "")),
    )


@router.get("/reports/attribution/latest", response_model=AttributionReport)
def attribution_latest() -> AttributionReport:
    try:
        rows = supabase.table("attribution_reports").select("*").order("created_at", desc=True).limit(1).execute().data or []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no attribution reports yet")
    return _attribution_row(rows[0])


@router.get("/reports/attribution/{deployment_id}", response_model=AttributionReport)
def attribution_by_deployment(deployment_id: str) -> AttributionReport:
    try:
        rows = supabase.table("attribution_reports").select("*").eq("deployment_id", deployment_id).limit(1).execute().data or []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no report for deployment {deployment_id}")
    return _attribution_row(rows[0])


# ─── Pipeline snapshot ────────────────────────────────────────────────

class PipelineSnapshot(BaseModel):
    accounts_total: int
    accounts_new_24h: int
    opportunities_by_stage: dict[str, int]
    deployments_live: int
    deployments_pending: int
    active_clients: int
    pending_reviews: int


@router.get("/reports/pipeline", response_model=PipelineSnapshot)
def pipeline_snapshot() -> PipelineSnapshot:
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    def _count_eq(table: str, col: str | None = None, val: str | None = None) -> int:
        q = supabase.table(table).select("*", count="exact", head=True)
        if col and val:
            q = q.eq(col, val)
        try:
            return q.execute().count or 0
        except Exception:  # noqa: BLE001
            return 0

    def _count_gte(table: str, col: str, val: str) -> int:
        try:
            return supabase.table(table).select("*", count="exact", head=True).gte(col, val).execute().count or 0
        except Exception:  # noqa: BLE001
            return 0

    stages = ["new_lead", "contacted", "engaged", "proposal_sent", "meeting_booked", "won", "lost"]
    opp_by_stage = {s: _count_eq("opportunities", "stage", s) for s in stages}

    return PipelineSnapshot(
        accounts_total=_count_eq("accounts"),
        accounts_new_24h=_count_gte("accounts", "created_at", since),
        opportunities_by_stage=opp_by_stage,
        deployments_live=_count_eq("deployments", "status", "live"),
        deployments_pending=_count_eq("deployments", "status", "pending"),
        active_clients=_count_eq("clients"),
        pending_reviews=_count_eq("founder_review_queue", "decision", "pending"),
    )


# ─── R&D digest ──────────────────────────────────────────────────────

class RDDigest(BaseModel):
    insights: list[dict]
    oss_candidates: list[dict]
    pending_proposals: list[dict]


@router.get("/reports/rd", response_model=RDDigest)
def rd_digest() -> RDDigest:
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    def _safe(table: str, q_fn) -> list[dict]:
        try:
            return q_fn(supabase.table(table)).execute().data or []
        except Exception:  # noqa: BLE001
            return []

    insights = _safe("rd_insights", lambda t: t.select(
        "insight_id,title,summary,impact_tag,estimated_priority,service_package_tag"
    ).gte("run_date", since_24h).order("estimated_priority", desc=False).limit(5))

    oss = _safe("rd_oss_candidates", lambda t: t.select(
        "candidate_id,name,repo_url,fit,risk,integration_type,recommendation,impact_tag"
    ).gte("run_date", since_7d).order("fit", desc=True).limit(3))

    proposals = _safe("rd_proposals", lambda t: t.select(
        "proposal_id,title,target_module,impact,effort,status"
    ).eq("status", "pending_review").order("created_at", desc=True))

    return RDDigest(insights=insights, oss_candidates=oss, pending_proposals=proposals)


# ─── Cost report ──────────────────────────────────────────────────────

class AgentCost(BaseModel):
    agent_name: str
    avg_cost_usd: float
    total_cost_usd: float
    runs_total: int


class CostReport(BaseModel):
    week_total_usd: float
    prior_week_total_usd: float | None = None
    agents: list[AgentCost]


@router.get("/reports/costs", response_model=CostReport)
def cost_report() -> CostReport:
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    two_weeks_ago = (date.today() - timedelta(days=14)).isoformat()

    def _week_rows(since: str, until: str) -> list[dict]:
        try:
            return supabase.table("agent_performance_metrics").select(
                "agent_name,avg_cost_usd,total_cost_usd,runs_total,metric_date"
            ).gte("metric_date", since).lt("metric_date", until).execute().data or []
        except Exception:  # noqa: BLE001
            return []

    current = _week_rows(week_ago, today)
    prior = _week_rows(two_weeks_ago, week_ago)

    week_total = sum(float(r.get("total_cost_usd") or 0) for r in current)
    prior_total = sum(float(r.get("total_cost_usd") or 0) for r in prior) if prior else None

    by_agent: dict[str, dict] = {}
    for r in current:
        name = r.get("agent_name", "unknown")
        if name not in by_agent:
            by_agent[name] = {"avg_cost_usd": 0.0, "total_cost_usd": 0.0, "runs_total": 0}
        by_agent[name]["total_cost_usd"] += float(r.get("total_cost_usd") or 0)
        by_agent[name]["runs_total"] += int(r.get("runs_total") or 0)
        by_agent[name]["avg_cost_usd"] = float(r.get("avg_cost_usd") or 0)

    agents = sorted(
        [AgentCost(agent_name=k, **v) for k, v in by_agent.items()],
        key=lambda a: a.total_cost_usd,
        reverse=True,
    )
    return CostReport(week_total_usd=round(week_total, 4), prior_week_total_usd=round(prior_total, 4) if prior_total is not None else None, agents=agents)


# ─── Client status ────────────────────────────────────────────────────

class ClientStatus(BaseModel):
    client_slug: str
    persona: str | None = None
    service_package: str | None = None
    stage: str | None = None
    latest_deployment_status: str | None = None
    latest_deployment_date: str | None = None
    latest_attribution_summary: str | None = None
    attribution_confidence: str | None = None
    case_study_status: str | None = None


@router.get("/clients/{slug}", response_model=ClientStatus)
def client_status(slug: str) -> ClientStatus:
    try:
        clients = supabase.table("clients").select("*").eq("client_slug", slug).limit(1).execute().data or []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    if not clients:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"client '{slug}' not found")
    c = clients[0]
    client_id = c.get("client_id")

    def _safe_get(table: str, filters: list[tuple]) -> dict:
        try:
            q = supabase.table(table).select("*")
            for col, val in filters:
                q = q.eq(col, val)
            rows = q.order("created_at", desc=True).limit(1).execute().data or []
            return rows[0] if rows else {}
        except Exception:  # noqa: BLE001
            return {}

    dep = _safe_get("deployments", [("client_id", client_id)]) if client_id else {}
    attr = _safe_get("attribution_reports", [("client_slug", slug)])
    cs = _safe_get("case_study_drafts", [("client_slug", slug)]) if slug else {}

    return ClientStatus(
        client_slug=slug,
        persona=c.get("persona"),
        service_package=c.get("service_package"),
        stage=c.get("stage"),
        latest_deployment_status=dep.get("status"),
        latest_deployment_date=str(dep.get("created_at", "")) or None,
        latest_attribution_summary=attr.get("summary_md"),
        attribution_confidence=attr.get("confidence"),
        case_study_status=cs.get("status"),
    )
