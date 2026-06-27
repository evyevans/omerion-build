"""browser-use integration for JS-rendered page extraction.

Provides a sync-callable fallback for pages that httpx can't read
(SPAs, JS-gated content, redirect-heavy sites). Wraps the browser-use
Agent with Claude Haiku running headless Playwright.

NOT used for LinkedIn (auth-gated regardless of JS rendering).
Activated only when _fetch_page() returns empty string.

Usage inside discover_sources:
    text = fetch_page_js(url, timeout=30)
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.agents.high_quality_lead_scraping.browser_scraper")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="browser-use")


async def _run_agent(url: str, timeout: float) -> str:
    """Navigate to URL and extract visible text via browser-use Agent."""
    try:
        from browser_use import Agent, BrowserProfile  # lazy — heavy dep
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        log.warning("browser_use_unavailable", error=str(exc))
        return ""

    llm = ChatAnthropic(
        model=settings.claude_model_haiku,
        api_key=settings.anthropic_api_key,
        temperature=0.0,
    )
    profile = BrowserProfile(headless=True)

    agent = Agent(
        task=(
            f"Navigate to {url}. "
            "Extract all readable text from the main content area. "
            "Return only the visible page text, nothing else. "
            "If the page requires login or shows a CAPTCHA, return the string BLOCKED."
        ),
        llm=llm,
        browser_profile=profile,
        use_vision=False,
        max_failures=2,
        use_thinking=False,
        enable_planning=False,
        max_actions_per_step=3,
    )
    try:
        result = await asyncio.wait_for(agent.run(max_steps=5), timeout=timeout)
        text = str(result.final_result() or "").strip()
        if text.upper() == "BLOCKED" or not text:
            return ""
        return text[:1500]
    except asyncio.TimeoutError:
        log.info("browser_scraper_timeout", url=url)
        return ""
    except Exception as exc:
        log.warning("browser_scraper_error", url=url, error=str(exc))
        return ""
    finally:
        try:
            await agent.browser_session.close()
        except Exception:
            pass


def fetch_page_js(url: str, timeout: float = 30.0) -> str:
    """Sync wrapper — run browser-use in a dedicated thread to avoid
    event-loop conflicts with LangGraph's sync nodes.

    Returns up to 1500 chars of visible page text, or "" on failure.
    """
    log.info("browser_scraper_start", url=url)

    def _run_in_thread() -> str:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run_agent(url, timeout))
        finally:
            loop.close()

    future = _executor.submit(_run_in_thread)
    try:
        return future.result(timeout=timeout + 5)
    except Exception as exc:
        log.warning("browser_scraper_thread_error", url=url, error=str(exc))
        return ""
