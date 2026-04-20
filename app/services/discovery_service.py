"""
Discovery service — orchestrates multi-source signal discovery.

Public interface
────────────────
    discover(query, db, *, limit, message_id) -> list[SignalCandidate]

Pipeline
────────
1. Resolve the enabled discovery sources from config.
2. Query all enabled sources in parallel via asyncio.gather.
3. Merge results, deduplicate by canonical URL.
4. Score each candidate with the relevance ranker.
5. Sort descending by score, return the top `limit` candidates.
6. Persist every returned candidate to the `signals` table.

This service stays deliberately small:
- source clients live in `app/integrations`
- ranking stays deterministic
- source failures are isolated and never abort the whole discovery call
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import aiosqlite

from app.core.config import settings
from app.db.queries import insert_signal
from app.integrations import arxiv_client, hn_client
from app.schemas.discovery import SignalCandidate, SourceType
from app.services import relevance_ranker

logger = logging.getLogger(__name__)


class DiscoveryFetcher(Protocol):
    async def __call__(
        self,
        query: str,
        *,
        max_results: int = 10,
    ) -> list[SignalCandidate]: ...


@dataclass(frozen=True)
class DiscoverySource:
    name: SourceType
    fetch: DiscoveryFetcher


def _source_registry() -> dict[str, DiscoverySource]:
    return {
        "arxiv": DiscoverySource(name="arxiv", fetch=arxiv_client.fetch),
        "hackernews": DiscoverySource(name="hackernews", fetch=hn_client.fetch),
    }


def get_enabled_sources() -> tuple[DiscoverySource, ...]:
    sources: list[DiscoverySource] = []
    registry = _source_registry()
    for source_name in settings.enabled_discovery_sources:
        source = registry.get(source_name)
        if source is None:
            logger.warning("Unknown discovery source configured: %r", source_name)
            continue
        sources.append(source)
    return tuple(sources)


def get_sources_by_name(names: Sequence[str]) -> tuple[DiscoverySource, ...]:
    registry = _source_registry()
    sources: list[DiscoverySource] = []
    for name in names:
        source = registry.get(name.strip().lower())
        if source is not None:
            sources.append(source)
    return tuple(sources)


async def _safe_fetch(
    source_name: str,
    operation: Awaitable[list[SignalCandidate]],
) -> list[SignalCandidate]:
    """Awaits a provider fetch, returning [] if the provider fails."""
    try:
        return await operation
    except Exception as exc:
        logger.warning("Discovery source %r failed: %s", source_name, exc)
        return []


def _normalize_url(raw_url: str) -> str:
    parts = urlsplit(raw_url)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _dedup(candidates: list[SignalCandidate]) -> list[SignalCandidate]:
    """Remove duplicates by canonical URL, preserving the highest-quality first hit."""
    seen: set[str] = set()
    unique: list[SignalCandidate] = []
    for candidate in candidates:
        key = _normalize_url(str(candidate.url))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _score(
    candidates: list[SignalCandidate],
    *,
    query: str | None = None,
) -> list[SignalCandidate]:
    """Score candidates and return them sorted by descending relevance."""
    scored: list[SignalCandidate] = []
    for candidate in candidates:
        score, note = relevance_ranker.rank(candidate, query=query)
        scored.append(
            candidate.model_copy(
                update={"relevance_score": score, "relevance_note": note}
            )
        )
    return sorted(scored, key=lambda item: item.relevance_score, reverse=True)


async def discover(
    query: str,
    db: aiosqlite.Connection,
    *,
    limit: int | None = None,
    message_id: int | None = None,
    sources: Sequence[DiscoverySource] | None = None,
) -> list[SignalCandidate]:
    """
    Run discovery for `query`, persist results, and return ranked candidates.

    `message_id` optionally links the discovered signal back to a triggering
    Telegram message row. This keeps Discovery usable both interactively and
    from future scheduled workflows.
    """
    resolved_limit = limit or settings.discovery_default_limit
    resolved_sources = tuple(sources) if sources is not None else get_enabled_sources()
    if not resolved_sources:
        logger.warning("Discovery skipped: no enabled sources.")
        return []

    per_source = max(resolved_limit * settings.discovery_fetch_multiplier, 10)
    source_results = await asyncio.gather(
        *[
            _safe_fetch(
                source.name,
                source.fetch(query, max_results=per_source),
            )
            for source in resolved_sources
        ]
    )

    merged = _dedup([candidate for results in source_results for candidate in results])
    ranked = _score(merged, query=query)
    top = ranked[:resolved_limit]

    for candidate in top:
        try:
            signal_id = await insert_signal(db, candidate, message_id=message_id)
            logger.info(
                "Persisted signal id=%d source_type=%r source_id=%r score=%.3f.",
                signal_id,
                candidate.source_type,
                candidate.source_id,
                candidate.relevance_score,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist signal source_id=%r: %s",
                candidate.source_id,
                exc,
            )

    logger.info(
        "Discovery complete: query=%r, sources=%d, candidates=%d, returned=%d.",
        query,
        len(resolved_sources),
        len(merged),
        len(top),
    )
    return top
