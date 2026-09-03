"""
Operator self-diagnosis: what is configured and whether the LLM answers.

Railway logs are not visible from Telegram, so when a post or plan comes
back "generado con fallback" Carlos needs a way to see *why* without leaving
the chat. The probe makes one tiny completion and reports the raw error
class and message when it fails. Keys are never echoed, only their presence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.core.config import settings
from app.integrations.openai_compat import (
    build_async_openai_client,
    completion_params,
)
from app.utils.text import trim_to_boundary

_PROBE_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class DiagReport:
    commit: str
    llm_model: str
    llm_base_host: str
    llm_key_present: bool
    llm_ok: bool
    llm_detail: str
    exa_key_present: bool
    anthropic_key_present: bool
    sources: tuple[str, ...]
    rss_feed_count: int
    goal_label: str | None


def commit_sha() -> str:
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get(
        "GIT_COMMIT_SHA", ""
    )
    return sha[:12] if sha else "unknown"


def llm_base_host() -> str:
    base = settings.openai_base_url.strip()
    if not base:
        return "api.openai.com"
    return urlsplit(base).netloc or base


async def llm_probe() -> tuple[bool, str]:
    """One minimal completion. Returns (ok, human-readable detail)."""
    if not settings.openai_api_key.strip():
        return False, "OPENAI_API_KEY vacía"
    client = build_async_openai_client(
        api_key=settings.openai_api_key,
        timeout_seconds=_PROBE_TIMEOUT_SECONDS,
    )
    try:
        response = await client.chat.completions.create(
            model=settings.editorial_model,
            **completion_params(settings.editorial_model, max_tokens=16),
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
    except Exception as exc:
        detail = trim_to_boundary(str(exc), 300) or exc.__class__.__name__
        return False, f"{exc.__class__.__name__}: {detail}"
    content = response.choices[0].message.content if response.choices else None
    return True, f"respondió {(content or '').strip()!r}"


async def build_report(goal_label: str | None) -> DiagReport:
    ok, detail = await llm_probe()
    return DiagReport(
        commit=commit_sha(),
        llm_model=settings.editorial_model,
        llm_base_host=llm_base_host(),
        llm_key_present=bool(settings.openai_api_key.strip()),
        llm_ok=ok,
        llm_detail=detail,
        exa_key_present=bool(settings.exa_api_key.strip()),
        anthropic_key_present=bool(settings.anthropic_api_key.strip()),
        sources=settings.enabled_discovery_sources,
        rss_feed_count=len(settings.discovery_rss_feed_list),
        goal_label=goal_label,
    )
