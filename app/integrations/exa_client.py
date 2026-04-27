"""
Exa neural search integration (Sub-phase B.6).

Exa returns links semantically close to the query, not keyword matches —
which fits our weekly's kitchen-sink query better than HN ever did.

Endpoint: POST https://api.exa.ai/search
Auth    : x-api-key header
Docs    : https://docs.exa.ai/reference/search

Behavior:
- Empty EXA_API_KEY raises RuntimeError so the orchestrator's _safe_fetch
  surfaces it as a failed source with a clear note ("EXA_API_KEY no
  configurada"), rather than silently returning zero candidates.
- We request `highlights` (3-sentence excerpts) and use them as summary;
  we do NOT request `text` (full body) — that would 10× the response size
  for marginal gain. If we ever want full body extraction, that's the
  Firecrawl-shaped problem deferred to Sub-phase D.
- Network failures propagate to the caller.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TypedDict, cast

import httpx

from app.core.config import settings
from app.schemas.discovery import SignalCandidate

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.exa.ai/search"
_USER_AGENT = "VelveteenInsightEngine/0.1 (https://github.com/velveteen)"
_TIMEOUT = 12.0
_SUMMARY_LIMIT = 500


class ExaResultDict(TypedDict, total=False):
    id: str
    title: str | None
    url: str
    publishedDate: str | None  # noqa: N815 — Exa uses camelCase
    highlights: list[str]
    score: float


def _parse_published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # Exa returns ISO 8601 (e.g. "2026-04-22T13:24:00.000Z").
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        logger.debug("Skipping malformed Exa publishedDate=%r.", raw)
        return None


def _clean_summary(highlights: list[str] | None, fallback: str) -> str:
    if not highlights:
        return fallback or ""
    joined = " ".join(part.strip() for part in highlights if part and part.strip())
    if not joined:
        return fallback or ""
    if len(joined) > _SUMMARY_LIMIT:
        # Trim to last sentence boundary within limit. Mirrors compact_text
        # policy — we never append ellipsis to summaries that flow into
        # downstream operator messages.
        window = joined[:_SUMMARY_LIMIT]
        for marker in (". ", "? ", "! "):
            idx = window.rfind(marker)
            if idx >= 80:
                return joined[: idx + 1].strip()
        space_idx = window.rfind(" ")
        if space_idx >= 80:
            return joined[:space_idx]
        return window
    return joined


def _parse_results(raw_results: list[ExaResultDict]) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []
    for result in raw_results:
        exa_id = str(result.get("id") or "").strip()
        title = (result.get("title") or "").strip()
        url = (result.get("url") or "").strip()
        if not exa_id or not title or not url:
            continue
        summary = _clean_summary(result.get("highlights"), title)
        published_at = _parse_published(result.get("publishedDate"))
        try:
            candidates.append(
                SignalCandidate(
                    source_type="exa",
                    source_id=exa_id,
                    title=title,
                    url=url,  # type: ignore[arg-type]
                    summary=summary,
                    raw_content=str(result)[:2000],
                    published_at=published_at,
                )
            )
        except Exception:
            logger.debug("Skipped malformed Exa result id=%r.", exa_id)
    return candidates


async def fetch(query: str, *, max_results: int = 10) -> list[SignalCandidate]:
    """
    Query Exa's neural search and return parsed SignalCandidates (unscored).

    Raises RuntimeError when EXA_API_KEY is not configured — discovery_service's
    _safe_fetch will catch it and surface a 'failed' outcome in the weekly's
    transparency footer instead of silently returning zero candidates.
    """
    api_key = settings.exa_api_key.strip()
    if not api_key:
        raise RuntimeError("EXA_API_KEY no configurada")

    transport = httpx.AsyncHTTPTransport(retries=2)
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT, "x-api-key": api_key},
        timeout=_TIMEOUT,
        transport=transport,
    ) as client:
        response = await client.post(
            _BASE_URL,
            json={
                "query": query,
                "type": "neural",
                "numResults": max_results,
                "useAutoprompt": True,
                "contents": {
                    "highlights": {
                        "numSentences": 3,
                        "highlightsPerUrl": 1,
                    },
                },
            },
        )
        response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Exa API payload: expected JSON object.")

    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raise RuntimeError("Unexpected Exa API payload: expected 'results' list.")

    typed_results = [
        cast(ExaResultDict, item) for item in raw_results if isinstance(item, dict)
    ]
    candidates = _parse_results(typed_results)
    logger.info(
        "Exa query=%r returned %d results, parsed %d candidates.",
        query,
        len(typed_results),
        len(candidates),
    )
    return candidates
