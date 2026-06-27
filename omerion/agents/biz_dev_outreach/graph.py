"""LangGraph for Biz-Dev Outreach / SEEK — Job Hunter sub-agent.

Flow:
    discover_postings        (multi-tier source fan-out)
      → filter_relevant      (Pinecone similarity vs Evykynn profile vector)
      → load_profile         (read resume.md + cover_letter.md into state)
      → rank_opportunities   (Sonnet — weighted rubric + scam zero-out)
      → draft_applications   (Sonnet — cover letter or cold-message per opportunity)
      → flag_risks           (deterministic HITL watchlist per draft)
      → hitl_review          (founder approves batch via Discord #seek)
      → hitl_wait
      → submit_applications  (Gmail for postings; queue for Upwork)
      → track_status         (mark ghost applications)
      → emit
"""
from __future__ import annotations

import json

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .prompts import RANK_SYSTEM, RANK_USER, REVIEW_CONTEXT_HEADER
from .state import SeekState
from .tools import (
    already_submitted,
    check_ghost_applications,
    dedup_postings,
    draft_application,
    embed_profile,
    fetch_ateam_rss,
    fetch_braintrust_rss,
    fetch_contra_rss,
    fetch_google_jobs,
    fetch_greenhouse_board,
    fetch_indeed_rss,
    fetch_lever_board,
    fetch_linkedin_jobs,
    fetch_toptal_rss,
    fetch_upwork_rss,
    fetch_wellfound_jobs,
    fetch_yc_jobs,
    flag_application_risks,
    index_posting_pinecone,
    load_cover_letter_template,
    load_resume,
    queue_upwork_application,
    score_postings,
    send_application_email,
    upsert_application,
    upsert_posting,
)

log = get_logger("omerion.agents.biz_dev_outreach")


# ─── Discovery ───────────────────────────────────────────────────────────────


@traced_node("discover_postings")
async def discover_node(state: SeekState) -> SeekState:
    """Fan out to every configured source in parallel using ThreadPoolExecutor.

    All 12 source-fetch functions are independent HTTP calls. Running them
    concurrently reduces wall time from ~36s sequential to ~3s (limited by the
    slowest single source). Each fetcher is a sync function, so we offload via
    run_in_executor rather than rewriting all fetchers as async.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    cfg = settings.agent("biz_dev_outreach")
    sources = cfg.get("sources", {})
    api_key = settings.firecrawl_api_key

    tier_s = sources.get("tier_s_invite_only", {})
    tier_a = sources.get("tier_a_high_signal", {})
    tier_b = sources.get("tier_b_volume", {})
    ats_boards = tier_a.get("growth_stage_ats_boards", [])
    lever_slugs = [b["slug"] for b in ats_boards if b.get("kind") == "lever"]
    greenhouse_slugs = [b["slug"] for b in ats_boards if b.get("kind") == "greenhouse"]

    fetch_fns = [
        lambda: fetch_toptal_rss(tier_s.get("toptal_rss", [])),
        lambda: fetch_ateam_rss(tier_s.get("ateam_rss", [])),
        lambda: fetch_braintrust_rss(tier_s.get("braintrust_rss", [])),
        lambda: fetch_contra_rss(tier_s.get("contra_rss", [])),
        lambda: fetch_wellfound_jobs(tier_a.get("wellfound_search_urls", []), api_key),
        lambda: fetch_yc_jobs(tier_a.get("yc_work_at_startup", []), api_key),
        lambda: fetch_lever_board(lever_slugs),
        lambda: fetch_greenhouse_board(greenhouse_slugs),
        lambda: fetch_upwork_rss(tier_b.get("upwork_rss_feeds", [])),
        lambda: fetch_indeed_rss(tier_b.get("indeed_rss_feeds", [])),
        lambda: fetch_linkedin_jobs(tier_b.get("linkedin_search_urls", []), api_key),
        lambda: fetch_google_jobs(tier_b.get("google_jobs_queries", [])),
    ]

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=12, thread_name_prefix="seek-discover") as executor:
        results = await asyncio.gather(
            *[loop.run_in_executor(executor, fn) for fn in fetch_fns],
            return_exceptions=True,
        )

    all_raw = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("seek_source_fetch_error", error=str(r))
        elif r:
            all_raw.extend(r)

    # Per-platform cap
    per_platform: dict[str, list] = {}
    max_pp = state.max_postings_per_platform
    for p in all_raw:
        per_platform.setdefault(p.platform, []).append(p)
    capped = []
    for bucket in per_platform.values():
        capped.extend(bucket[:max_pp])

    state.raw_postings = dedup_postings(capped)
    state.skipped_duplicate = len(capped) - len(state.raw_postings)
    log.info(
        "seek_discover_complete",
        raw=len(capped),
        new=len(state.raw_postings),
        dupes=state.skipped_duplicate,
        sources=len(per_platform),
    )
    return state


@traced_node("filter_relevant")
def filter_node(state: SeekState) -> SeekState:
    if not state.raw_postings:
        return state

    profile_vector = embed_profile(state.resume_text or "founder profile placeholder",
                                    state.cover_letter_template or "")
    scored = score_postings(state.raw_postings, profile_vector)
    relevant = [p for p in scored if p.relevance_score >= state.min_relevance_score]
    state.skipped_low_relevance = len(scored) - len(relevant)
    state.relevant_postings = relevant
    # raw_postings no longer needed — clear to shrink checkpoint blobs for all
    # subsequent nodes (ranked_postings + drafts are what matters from here on).
    state.raw_postings = []
    log.info("seek_filter_complete", relevant=len(relevant), skipped=state.skipped_low_relevance)
    return state


_PROFILE_MAX_CHARS = 8192  # cap inline-state blobs to ~8KB each for checkpoint hygiene

@traced_node("load_profile")
def load_profile_node(state: SeekState) -> SeekState:
    resume = load_resume()
    cover = load_cover_letter_template()
    state.resume_text = resume[:_PROFILE_MAX_CHARS]
    state.cover_letter_template = cover[:_PROFILE_MAX_CHARS]
    log.info("seek_profile_loaded",
             resume_chars=len(resume),
             cover_letter_chars=len(cover),
             resume_truncated=len(resume) > _PROFILE_MAX_CHARS,
             cover_truncated=len(cover) > _PROFILE_MAX_CHARS)
    return state


# ─── Ranking (LLM-driven, weighted rubric, scam zero-out) ────────────────────


@traced_node("rank_opportunities")
def rank_node(state: SeekState) -> SeekState:
    if not state.relevant_postings:
        return state

    cfg = settings.agent("biz_dev_outreach")
    top_n = int(cfg.get("top_n_to_draft", 5))
    forbidden = cfg.get("forbidden_company_keywords", [])
    specialties = cfg.get("target_categories", [])

    opportunities = [
        {
            "external_id": p.external_id,
            "platform": p.platform,
            "title": p.title,
            "company": p.company,
            "description": (p.description or "")[:1200],
            "budget_low": p.budget_low,
            "budget_high": p.budget_high,
            "budget_type": p.budget_type,
            "remote": p.remote,
        }
        for p in state.relevant_postings
    ]

    router = ClaudeRouter()
    user_prompt = RANK_USER.format(
        specialties=", ".join(specialties) if specialties else "AI Automation Consulting",
        forbidden_company_keywords=", ".join(forbidden) if forbidden else "(none)",
        opportunities_json=json.dumps(opportunities, ensure_ascii=False),
    )

    rank_map: dict[str, dict] = {}
    try:
        resp = router.complete(
            tier=Tier.FAST,  # Haiku for structured ranking JSON
            system=RANK_SYSTEM,
            prompt=user_prompt,
            max_tokens=2000,
            temperature=0.1,
        )
        text = resp.get("text", "[]").strip()
        # Tolerate code-fence wrapping
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = json.loads(text)
        for item in parsed:
            rank_map[str(item.get("external_id", ""))] = item
    except Exception as exc:  # noqa: BLE001
        log.warning("seek_rank_llm_failed_fallback", error=str(exc))
        # Deterministic fallback — relevance * budget bonus
        for p in state.relevant_postings:
            bonus = 1.0
            if p.budget_low and p.budget_type == "hourly" and p.budget_low >= 50:
                bonus = 1.3
            elif p.budget_low and p.budget_type == "fixed" and p.budget_low >= 500:
                bonus = 1.2
            rank_map[p.external_id] = {
                "rank_score": round(p.relevance_score * bonus * 10, 2),
                "rationale": "deterministic fallback (LLM rank failed)",
                "skip_reason": None,
            }

    # Apply scores back to postings + count skips
    for p in state.relevant_postings:
        scored = rank_map.get(p.external_id, {})
        p.rank_score = float(scored.get("rank_score", 0.0))
        p.rank_rationale = scored.get("rationale", "")
        skip_reason = scored.get("skip_reason")
        if skip_reason in ("scam_signal", "missing_budget"):
            state.skipped_scam += 1

    # Filter: keep only opportunities meeting submission threshold, sorted desc
    eligible = [p for p in state.relevant_postings if p.rank_score >= state.min_rank_score]
    eligible.sort(key=lambda p: p.rank_score, reverse=True)
    ranked = eligible[:top_n]
    for i, p in enumerate(ranked):
        p.rank = i + 1

    state.skipped_low_rank = len(state.relevant_postings) - len(ranked)
    state.ranked_postings = ranked
    log.info("seek_rank_complete", ranked=len(ranked), skipped_low_rank=state.skipped_low_rank,
             skipped_scam=state.skipped_scam, avg_score=round(
                 sum(p.rank_score for p in ranked) / len(ranked), 2) if ranked else 0.0)
    return state


# ─── Drafting ────────────────────────────────────────────────────────────────


@traced_node("draft_applications")
def draft_node(state: SeekState) -> SeekState:
    if not state.ranked_postings:
        log.info("seek_no_postings_to_draft")
        return state

    router = ClaudeRouter()
    for posting in state.ranked_postings:
        try:
            # run_id threads through so Langfuse traces group under the right
            # session AND the router's per-run budget tracker keys correctly.
            draft = draft_application(
                router, posting, state.resume_text, state.cover_letter_template,
                run_id=state.run_id,
            )
            # Skip-token convention: if model returned literal "SKIP" as cover letter, drop it.
            if draft.cover_letter_body.strip().upper() == "SKIP":
                log.info("seek_draft_skipped_by_model", posting_id=str(posting.posting_id))
                continue
            state.drafts.append(draft)
        except Exception as exc:  # noqa: BLE001
            log.error("seek_draft_error", posting_id=str(posting.posting_id), error=str(exc))
            state.record_error("draft_applications", exc)
    log.info("seek_drafts_complete", count=len(state.drafts))
    return state


# ─── Risk flagging (deterministic HITL watchlist) ────────────────────────────


@traced_node("flag_risks")
def flag_risks_node(state: SeekState) -> SeekState:
    if not state.drafts:
        return state

    cfg = settings.agent("biz_dev_outreach")
    forbidden = cfg.get("forbidden_company_keywords", [])
    thresholds = cfg.get("flag_thresholds", {})
    posting_by_id = {p.posting_id: p for p in state.ranked_postings}

    flagged_count = 0
    for draft in state.drafts:
        posting = posting_by_id.get(draft.posting_id)
        if posting is None:
            continue
        flags, notes = flag_application_risks(
            draft=draft,
            posting=posting,
            prior_drafts=state.drafts,
            forbidden_company_keywords=forbidden,
            flag_thresholds=thresholds,
        )
        draft.hitl_flags = flags
        draft.hitl_notes = notes
        if flags:
            flagged_count += 1

    state.drafts_with_flags = flagged_count
    log.info("seek_flag_risks_complete",
             total=len(state.drafts), flagged=flagged_count)
    return state


# ─── HITL ────────────────────────────────────────────────────────────────────


@traced_node("hitl_review")
def hitl_review_node(state: SeekState) -> SeekState:
    if not state.drafts:
        return state

    n_postings = sum(1 for d in state.drafts if d.kind == "posting")
    n_outreach = sum(1 for d in state.drafts if d.kind == "outreach_target")
    platforms = sorted({d.platform for d in state.drafts})
    avg_rank = (sum(d.rank_score for d in state.drafts) / len(state.drafts)) if state.drafts else 0.0

    body = REVIEW_CONTEXT_HEADER.format(
        run_date=state.run_date.isoformat(),
        n_postings=n_postings,
        n_outreach=n_outreach,
        n_drafts=len(state.drafts),
        platforms=", ".join(platforms),
        avg_rank=avg_rank,
        n_flagged=state.drafts_with_flags,
    )

    posting_by_id = {p.posting_id: p for p in state.ranked_postings}
    for draft in state.drafts:
        posting = posting_by_id.get(draft.posting_id)
        title = posting.title if posting else "Unknown"
        flag_line = ""
        if draft.hitl_flags:
            flag_line = f"\n🚩 **Flags:** `{'`, `'.join(draft.hitl_flags)}`  — {draft.hitl_notes}\n"
        body += (
            f"\n\n---\n\n**{title}** ({draft.platform}, {draft.kind})  "
            f"·  rank `{draft.rank_score:.1f}/10`{flag_line}\n"
        )
        if posting:
            body += f"_URL:_ {posting.url}\n\n"
        if draft.kind == "posting":
            if draft.subject_line:
                body += f"_Subject:_ {draft.subject_line}\n\n"
            body += f"**Cover Letter:**\n{draft.cover_letter_body}\n"
            if draft.proposal_body:
                body += f"\n**Proposal (Upwork):**\n{draft.proposal_body}\n"
        else:
            body += f"**Outreach Message:**\n{draft.outreach_message}\n"

    review = create_founder_review_task(
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        subject=f"SEEK batch — {state.run_date.isoformat()} "
                f"({len(state.drafts)} opportunities, {state.drafts_with_flags} flagged)",
        context_md=body,
        draft_ref={
            "kind": "seek_batch",
            "draft_count": len(state.drafts),
            "flagged_count": state.drafts_with_flags,
        },
        correlation_id=state.correlation_id,
    )
    state.review_id = review["review_id"]
    return state


@traced_node("hitl_wait")
def hitl_wait_node(state: SeekState) -> SeekState:
    if not state.review_id:
        return state
    # Replay guard: if a decision is already resolved on state (e.g. the graph is
    # re-entered from a checkpoint after the resume value was applied), do NOT
    # call interrupt() again — re-interrupting blocks forever waiting for a
    # second resolution. Critical for this G1 sender (real application emails).
    if state.decision in ("approved", "rejected"):
        return state
    result = interrupt({"review_id": str(state.review_id), "session_id": state.session_id})
    decisions = result.get("decisions", {})
    state.decision = decisions.get(str(state.review_id), "rejected")
    state.scratch["decision_notes"] = result.get("decision_notes")
    return state


# ─── Submission + lifecycle ──────────────────────────────────────────────────


@traced_node("submit_applications")
def submit_node(state: SeekState) -> SeekState:
    if state.decision != "approved":
        log.info("seek_batch_rejected", count=len(state.drafts))
        return state

    posting_by_id = {p.posting_id: p for p in state.ranked_postings}

    for draft in state.drafts:
        posting = posting_by_id.get(draft.posting_id)
        if posting is None:
            continue

        # G1 idempotency: if this posting already has a terminal application
        # (sent/queued/replied) from a prior run, skip entirely — re-running
        # upsert_application would reset its status to 'drafted' and re-send a
        # live application email. Don't re-count or re-emit either.
        if already_submitted(draft.posting_id):
            log.info("seek_submit_skip_already_submitted", posting_id=str(draft.posting_id))
            continue

        # Wave 6 v1: belt-and-suspenders style-filter gate. The flag was
        # already surfaced in the founder review card (see draft_application
        # in tools.py), but a batch-level "approved" decision could carry one
        # bad draft through. Refuse to send anything that the router-level
        # style filter marked as a violation, regardless of batch approval.
        # The founder can re-run / re-prompt to get a clean draft.
        if "style_filter" in draft.hitl_flags:
            log.warning(
                "seek_submit_skip_style_filter",
                posting_id=str(draft.posting_id),
                hitl_notes=draft.hitl_notes,
            )
            continue

        upsert_posting(posting)
        upsert_application(draft, state.run_id, state.review_id)

        try:
            pinecone_id = index_posting_pinecone(posting)
            posting.pinecone_id = pinecone_id
            from omerion_core.clients.supabase_client import supabase
            supabase.table("job_postings").update(
                {"pinecone_id": pinecone_id}
            ).eq("platform", posting.platform).eq("external_id", posting.external_id).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("seek_pinecone_index_error", error=str(exc))

        if draft.platform == "upwork":
            queue_upwork_application(draft)
            draft.approved = True
            state.submitted_count += 1
        else:
            provider_ref = send_application_email(draft, posting)
            if provider_ref:
                draft.approved = True
                state.submitted_count += 1

    log.info("seek_submitted", count=state.submitted_count)
    return state


@traced_node("track_status")
def track_node(state: SeekState) -> SeekState:
    cfg = settings.agent("biz_dev_outreach")
    threshold = cfg.get("ghost_threshold_days", 14)
    ghosts = check_ghost_applications(threshold)
    if ghosts:
        log.info("seek_ghost_applications_found", count=len(ghosts))
        state.scratch["ghost_application_ids"] = [g["application_id"] for g in ghosts]
    return state


@traced_node("emit")
def emit_node(state: SeekState) -> SeekState:
    for posting in state.ranked_postings:
        emit_event(
            EventType.JOB_POSTING_DISCOVERED,
            source_agent=state.agent_name,
            payload={
                "posting_id": str(posting.posting_id),
                "platform": posting.platform,
                "kind": posting.kind,
                "title": posting.title,
                "relevance_score": posting.relevance_score,
                "rank_score": posting.rank_score,
            },
            correlation_id=state.correlation_id,
        )

    if state.decision != "approved":
        return state

    for draft in state.drafts:
        if not draft.approved:
            continue
        emit_event(
            EventType.APPLICATION_SENT,
            source_agent=state.agent_name,
            payload={
                "application_db_id": str(draft.application_db_id or ""),
                "posting_id": str(draft.posting_id),
                "platform": draft.platform,
                "kind": draft.kind,
                "rank_score": draft.rank_score,
                "hitl_flags": draft.hitl_flags,
            },
            correlation_id=state.correlation_id,
        )

    for app_id in state.scratch.get("ghost_application_ids", []):
        emit_event(
            EventType.APPLICATION_GHOSTED,
            source_agent=state.agent_name,
            payload={"application_id": str(app_id)},
            correlation_id=state.correlation_id,
        )
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(SeekState)
    g.add_node("discover_postings", discover_node)
    g.add_node("filter_relevant", filter_node)
    g.add_node("load_profile", load_profile_node)
    g.add_node("rank_opportunities", rank_node)
    g.add_node("draft_applications", draft_node)
    g.add_node("flag_risks", flag_risks_node)
    g.add_node("hitl_review", hitl_review_node)
    g.add_node("hitl_wait", hitl_wait_node)
    g.add_node("submit_applications", submit_node)
    g.add_node("track_status", track_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("discover_postings")
    # load_profile runs BEFORE filter_relevant — filter_relevant reads
    # state.resume_text to build the profile vector for similarity scoring.
    g.add_edge("discover_postings", "load_profile")
    g.add_edge("load_profile", "filter_relevant")
    g.add_edge("filter_relevant", "rank_opportunities")
    g.add_edge("rank_opportunities", "draft_applications")
    g.add_edge("draft_applications", "flag_risks")
    g.add_edge("flag_risks", "hitl_review")
    g.add_edge("hitl_review", "hitl_wait")
    g.add_edge("hitl_wait", "submit_applications")
    g.add_edge("submit_applications", "track_status")
    g.add_edge("track_status", "emit")
    g.add_edge("emit", END)
    return g.compile(checkpointer=get_checkpointer())
