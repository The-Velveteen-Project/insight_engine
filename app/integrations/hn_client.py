"""
Hacker News integration — Algolia HN Search API (free, no API key, stable).

Endpoint: https://hn.algolia.com/api/v1/search
Docs    : https://hn.algolia.com/api

Returns `story` hits only. Each hit's `url` field is the external article link;
`objectID` is the HN item ID (used as source_id and for the HN discussion link).

Design choices:
- Pure JSON — no extra deps beyond httpx.
- `story_text` (self-posts) used as summary fallback when no external URL.
- Network failures propagate to the caller (discovery_service catches them).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from html import unescape
from typing import TypedDict, cast

import httpx

from app.schemas.discovery import SignalCandidate

logger = logging.getLogger(__name__)

_BASE_URL = "https://hn.algolia.com/api/v1/search"
_USER_AGENT = "VelveteenInsightEngine/0.1 (https://github.com/velveteen)"
_TIMEOUT = 10.0
_HN_ITEM_BASE = "https://news.ycombinator.com/item?id="
_TAG_RE = re.compile(r"<[^>]+>")


class HackerNewsHit(TypedDict, total=False):
    objectID: str
    title: str | None
    url: str | None
    story_text: str | None
    created_at_i: int | None


def _parse_timestamp(ts: int | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


def _clean_summary(raw_story_text: str | None, fallback: str) -> str:
    if not raw_story_text:
        return fallback
    cleaned = unescape(_TAG_RE.sub(" ", raw_story_text))
    compact = " ".join(cleaned.split())
    return compact or fallback


def _parse_hits(hits: list[HackerNewsHit]) -> list[SignalCandidate]:
    candidates: list[SignalCandidate] = []

    for hit in hits:
        hn_id: str = str(hit.get("objectID", ""))
        title: str = hit.get("title") or ""
        # External URL if it's a link post; HN discussion URL for text posts
        raw_url: str = hit.get("url") or f"{_HN_ITEM_BASE}{hn_id}"
        summary = _clean_summary(hit.get("story_text"), title)
        if len(summary) > 500:
            summary = summary[:497] + "…"

        published_at = _parse_timestamp(hit.get("created_at_i"))

        if not hn_id or not title or not raw_url:
            continue

        try:
            candidates.append(
                SignalCandidate(
                    source_type="hackernews",
                    source_id=hn_id,
                    title=title,
                    url=raw_url,  # type: ignore[arg-type]
                    summary=summary,
                    raw_content=str(hit)[:2000],
                    published_at=published_at,
                )
            )
        except Exception:
            # Malformed URL or unexpected structure — skip this hit silently
            logger.debug("Skipped malformed HN hit objectID=%r.", hn_id)

    return candidates


async def fetch(query: str, *, max_results: int = 10) -> list[SignalCandidate]:
    """
    Query the HN Algolia API and return parsed SignalCandidates (unscored).

    Args:
        query       : free-text search string
        max_results : number of hits to retrieve
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT,
    ) as client:
        response = await client.get(
            _BASE_URL,
            params={
                "query": query,
                "tags": "story",
                "hitsPerPage": max_results,
            },
        )
        response.raise_for_status()

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected HN API payload: expected JSON object.")

    raw_hits = payload.get("hits", [])
    if not isinstance(raw_hits, list):
        raise RuntimeError("Unexpected HN API payload: expected 'hits' list.")

    hits = [cast(HackerNewsHit, hit) for hit in raw_hits if isinstance(hit, dict)]
    candidates = _parse_hits(hits)
    logger.info(
        "HN query=%r returned %d hits, parsed %d candidates.",
        query,
        len(hits),
        len(candidates),
    )
    return candidates
