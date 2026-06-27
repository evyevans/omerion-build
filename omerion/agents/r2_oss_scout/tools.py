"""Tools for R2 OSS Scout."""
from __future__ import annotations

from typing import Iterable
from uuid import UUID

import httpx

from omerion_core.clients.supabase_client import supabase
from omerion_core.http import PermanentHTTPError, TransientHTTPError, safe_request
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings

from .prompts import ANALYZE_SYSTEM, ANALYZE_USER
from .state import RepoCandidate, RubricScore, ScoredCandidate

log = get_logger("omerion.agents.r2_oss_scout")

_VALID_INTEGRATIONS = {"component", "pattern", "full_module", "reference_only"}
_VALID_TAGS = {"daam", "capa", "remi", "asap", "internal_os"}
_RISKY_LICENSES = {"GPL-2.0", "GPL-3.0", "AGPL-3.0", "SSPL-1.0"}

_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
_GITHUB_README_URL = "https://api.github.com/repos/{full_name}/readme"


def _github_headers() -> dict:
    token = settings.github_token
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_readme(full_name: str) -> str:
    """Fetch and decode the README for a repo. Returns first 3000 chars."""
    import base64
    try:
        # 404 means no README exists — common, not an error. Allow it through the
        # expected_status set so safe_request doesn't raise PermanentHTTPError on it.
        resp = safe_request(
            "GET", _GITHUB_README_URL.format(full_name=full_name),
            service="github",
            headers=_github_headers(),
            timeout=10.0,
            attempts=2,
            expected_status=(200, 404),
        )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64":
            decoded = base64.b64decode(content.replace("\n", "")).decode("utf-8", errors="ignore")
            return decoded[:3000]
        return content[:3000]
    except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
        log.warning("r2_readme_fetch_failed", repo=full_name, error=str(exc),
                    error_class=type(exc).__name__)
        return ""


def search_github(tags: list[str]) -> list[RepoCandidate]:
    """Search GitHub for repos matching each tag using the Search API.

    Requires GITHUB_TOKEN in .env for higher rate limits (30 req/min vs 10).
    Each tag becomes one search query; top 10 results are collected per tag.
    """
    if not tags:
        return []

    token = settings.github_token
    if not token:
        log.warning("r2_github_token_missing", hint="Set GITHUB_TOKEN in .env for higher rate limits")

    candidates: list[RepoCandidate] = []
    seen: set[str] = set()

    for tag in tags:
        query = f"{tag} in:name,description,topics"
        try:
            # GitHub Search API: 30 req/min authenticated; the "github_search" bucket
            # rate-limits to 0.5 req/sec. safe_request handles 5xx + 429 retries.
            resp = safe_request(
                "GET", _GITHUB_SEARCH_URL,
                service="github_search",
                headers=_github_headers(),
                params={"q": query, "sort": "stars", "order": "desc", "per_page": 10},
                timeout=15.0,
                attempts=3,
            )
            items = resp.json().get("items", [])
        except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
            log.warning("r2_github_search_error", tag=tag, error=str(exc),
                        error_class=type(exc).__name__)
            continue

        for item in items:
            full_name = item.get("full_name", "")
            if not full_name or full_name in seen:
                continue
            seen.add(full_name)

            license_info = item.get("license") or {}
            # Token bucket on the "github" service paces README fetches; no
            # manual time.sleep() needed.
            readme = _fetch_readme(full_name)

            candidates.append(RepoCandidate(
                repo_url=item.get("html_url", f"https://github.com/{full_name}"),
                name=full_name,
                description=(item.get("description") or "")[:500],
                stars=int(item.get("stargazers_count") or 0),
                language=item.get("language"),
                license=license_info.get("spdx_id"),
                last_commit=item.get("pushed_at"),
                readme_excerpt=readme,
                search_tag=tag,
            ))

        # Inter-tag pacing is now handled by the "github_search" token bucket
        # acquired inside safe_request() — explicit sleep is redundant.

    log.info("r2_github_search_complete", tags=len(tags), found=len(candidates))
    return candidates


def discover_candidates(seed_terms: list[str] | None = None) -> list[RepoCandidate]:
    """Discover repo candidates from GitHub.

    When `seed_terms` are provided (derived from the triggering R1 insight), the
    search is FOCUSED on them — so an R1 signal like "LangGraph 1.0 released"
    actually steers R2 toward LangGraph repos — with a couple of the static tags
    kept as anchors. Cron runs (no seed) fall back to the full static tag list.
    """
    cfg = settings.agent("r2_oss_scout")
    tags = cfg.get("search_tags", [])
    if seed_terms:
        queries = list(dict.fromkeys([t for t in seed_terms if t] + tags[:2]))
        return search_github(queries)
    return search_github(tags)


# impact_tag → a domain keyword that anchors the GitHub search when R1 seeds R2.
_TAG_SEED = {
    "daam": "CRM automation agent",
    "capa": "ops workflow automation",
    "remi": "market research automation",
    "asap": "process automation workflow",
    "internal_os": "agent orchestration framework",
}


def seed_terms_from_insight(title: str, impact_tag: str = "") -> list[str]:
    """Build focused GitHub search terms from a triggering R1 insight.

    Returns [] for cron runs (no insight) so discovery falls back to static tags.
    """
    terms: list[str] = []
    t = (title or "").strip()
    if t:
        terms.append(t[:80])
    domain = _TAG_SEED.get((impact_tag or "").lower())
    if domain:
        terms.append(domain)
    return terms


def passes_floor(repo: RepoCandidate) -> bool:
    cfg = settings.agent("r2_oss_scout")
    # internal_os repos bypass the community-signal threshold
    floor = 50 if repo.search_tag == "internal_os" else int(cfg.get("min_stars", 150))
    return (repo.stars or 0) >= floor


def _clamp(v, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, x))


def _overall(rubric: RubricScore) -> float:
    # Weighted composite: fit 0.4, maturity 0.25, composability 0.25, risk penalty 0.1
    return round(
        rubric.fit * 0.4
        + rubric.maturity * 0.25
        + rubric.composability * 0.25
        + (1 - rubric.risk) * 0.10,
        3,
    )


def _analyze_with_tier(router: ClaudeRouter, repo: RepoCandidate, tier: Tier) -> tuple[dict, bool]:
    resp = router.complete(
        system=ANALYZE_SYSTEM,
        prompt=ANALYZE_USER.format(
            name=repo.name,
            repo_url=repo.repo_url,
            stars=repo.stars,
            language=repo.language or "unknown",
            license=repo.license or "unknown",
            description=repo.description[:500],
            search_tag=repo.search_tag,
            readme_excerpt=repo.readme_excerpt[:3000],
        ),
        tier=tier,
        max_tokens=500,
        temperature=0.2,
    )
    return extract_json_object(resp["text"])


def _rubric_from_data(data: dict, repo_license: str | None) -> RubricScore:
    rubric = RubricScore(
        fit=_clamp(data.get("fit")),
        maturity=_clamp(data.get("maturity")),
        composability=_clamp(data.get("composability")),
        risk=_clamp(data.get("risk")),
    )
    if repo_license in _RISKY_LICENSES:
        rubric.risk = max(rubric.risk, 0.8)
    rubric.overall = _overall(rubric)
    return rubric


def analyze_repo(router: ClaudeRouter, repo: RepoCandidate) -> ScoredCandidate | None:
    # Base scoring on Haiku (Tier.FAST); escalate to Sonnet only for risky repos.
    data, _ok = _analyze_with_tier(router, repo, Tier.FAST)
    integration = str(data.get("integration_type", "")).lower()
    impact = str(data.get("impact_tag", "")).lower()
    if integration not in _VALID_INTEGRATIONS or impact not in _VALID_TAGS:
        return None

    rubric = _rubric_from_data(data, repo.license)
    scored_by: str = "haiku"

    if rubric.risk > 0.5:
        log.info("r2_sonnet_escalation", repo=repo.name, haiku_risk=round(rubric.risk, 3))
        try:
            # Tier.DEFAULT = Sonnet. (Was Tier.POWER — a non-existent enum member that
            # AttributeError'd here, so the escalation was dead and scored_by lied.)
            sonnet_data, sonnet_ok = _analyze_with_tier(router, repo, Tier.DEFAULT)
            s_integration = str(sonnet_data.get("integration_type", "")).lower()
            s_impact = str(sonnet_data.get("impact_tag", "")).lower()
            if sonnet_ok and s_integration in _VALID_INTEGRATIONS and s_impact in _VALID_TAGS:
                rubric = _rubric_from_data(sonnet_data, repo.license)
                integration = s_integration
                impact = s_impact
                data = sonnet_data
                scored_by = "sonnet"
                log.info("r2_sonnet_result", repo=repo.name, sonnet_risk=round(rubric.risk, 3))
        except Exception as exc:  # noqa: BLE001
            log.warning("r2_sonnet_escalation_failed", repo=repo.name, error=str(exc))

    return ScoredCandidate(
        repo=repo,
        rubric=rubric,
        integration_type=integration,  # type: ignore[arg-type]
        impact_tag=impact,  # type: ignore[arg-type]
        recommendation=str(data.get("recommendation", "")).strip(),
        scored_by=scored_by,  # type: ignore[arg-type]
    )


def _existing_urls(urls: Iterable[str]) -> set[str]:
    urls = list(urls)
    if not urls:
        return set()
    resp = supabase.table("rd_oss_candidates").select("repo_url").in_("repo_url", urls).execute()
    return {row["repo_url"] for row in (resp.data or [])}


def persist_candidates(scored: list[ScoredCandidate]) -> tuple[int, int]:
    from datetime import date as _date
    import json

    if not scored:
        return 0, 0
    seen = _existing_urls(s.repo.repo_url for s in scored)
    fresh = [s for s in scored if s.repo.repo_url not in seen]
    rescore = [s for s in scored if s.repo.repo_url in seen]

    # Fresh candidates: insert with history initialized to current run
    if fresh:
        today = str(_date.today())
        rows = [{
            "repo_url": s.repo.repo_url,
            "name": s.repo.name,
            "description": s.repo.description,
            "stars": s.repo.stars,
            "language": s.repo.language,
            "license": s.repo.license,
            "integration_type": s.integration_type,
            "impact_tag": s.impact_tag,
            "rubric": s.rubric.model_dump(),
            "recommendation": s.recommendation,
            "overall_score": s.rubric.overall,
            "scored_by": s.scored_by,
            "rescore_history": json.dumps([{
                "run_date": today,
                "overall": round(s.rubric.overall, 4),
                "fit": round(s.rubric.fit, 4),
                "maturity": round(s.rubric.maturity, 4),
                "composability": round(s.rubric.composability, 4),
                "risk": round(s.rubric.risk, 4),
            }]),
        } for s in fresh]
        resp = supabase.table("rd_oss_candidates").upsert(rows, on_conflict="repo_url").execute()
        written = len(resp.data or [])
        for row, original in zip(resp.data or [], fresh):
            original.candidate_id = UUID(row["candidate_id"])
    else:
        written = 0

    # Rescore candidates: append to existing history via RPC
    for s in rescore:
        entry = {
            "run_date": str(_date.today()),
            "overall": round(s.rubric.overall, 4),
            "fit": round(s.rubric.fit, 4),
            "maturity": round(s.rubric.maturity, 4),
            "composability": round(s.rubric.composability, 4),
            "risk": round(s.rubric.risk, 4),
        }
        try:
            supabase.rpc("r2_append_rescore_history", {
                "p_repo_url": s.repo.repo_url,
                "p_entry": entry,
            }).execute()
        except Exception as exc:
            log.warning("r2_rescore_history_append_failed", repo_url=s.repo.repo_url, error=str(exc))

    return written, len(rescore)
