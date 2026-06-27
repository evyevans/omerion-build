"""DeepSeek (coder) and Qwen (planner) fallbacks.

Both services expose OpenAI-compatible chat completions endpoints.
Reused only when the primary Claude path fails.
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from omerion_core.settings import settings


def _post_chat(base_url: str, api_key: str, model: str,
               system: str | None, messages: list[dict[str, Any]],
               max_tokens: int, temperature: float) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    with httpx.Client(timeout=60) as c:
        resp = c.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


@retry(reraise=True, stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=10))
def deepseek_complete(*, system: str | None, messages: list[dict[str, Any]],
                      max_tokens: int = 2048, temperature: float = 0.2) -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set; cannot fall back")
    return _post_chat(
        settings.deepseek_base_url, settings.deepseek_api_key,
        "deepseek-chat", system, messages, max_tokens, temperature,
    )


@retry(reraise=True, stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=10))
def qwen_complete(*, system: str | None, messages: list[dict[str, Any]],
                  max_tokens: int = 2048, temperature: float = 0.2) -> str:
    if not settings.qwen_api_key:
        raise RuntimeError("QWEN_API_KEY not set; cannot fall back")
    return _post_chat(
        settings.qwen_base_url, settings.qwen_api_key,
        "qwen-max", system, messages, max_tokens, temperature,
    )
