"""
RSS / Atom feed source for discovery.

Pulls the latest entries from a small, configured list of editorial feeds
(research blogs, lab announcements) that neither arXiv nor Hacker News cover
well. Feeds are set with DISCOVERY_RSS_FEEDS (comma-separated URLs).

Behavior:
- Every feed is fetched in parallel; one failing feed does not hide the rest.
- Entries are pre-filtered by query token overlap, then by recency, so a
  narrow query surfaces matching posts and a broad one falls back to the
  newest entries. The relevance ranker still scores whatever is returned.
- HTML in descriptions is stripped; summaries are trimmed on a sentence or
  word boundary, never with an ellipsis.
- Uses only the standard library XML parser: no scraping, no extra deps.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.schemas.discovery import SignalCandidate
from app.utils.text import trim_to_boundary

logger = logging.getLogger(__name__)

_USER_AGENT = "VelveteenInsightEngine/0.1 (+https://github.com/The-Velveteen-Project)"
_TIMEOUT = 12.0
_SUMMARY_LIMIT = 500
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_TAG_RE = re.compile(r"<[^>]+>")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-]{2,}")


def _clean_html(raw: str | None) -> str:
    if not raw:
        return ""
    return html.unescape(_TAG_RE.sub(" ", raw))


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return (element.text or "").strip()


def _rss_items(root: ET.Element) -> list[tuple[str, str, str, str, str]]:
    """Returns (id, title, link, summary, date) tuples for RSS 2.0 channels."""
    items: list[tuple[str, str, str, str, str]] = []
    for item in root.iter("item"):
        link = _text(item.find("link"))
        guid = _text(item.find("guid")) or link
        items.append(
            (
                guid,
                _text(item.find("title")),
                link,
                _text(item.find("description")),
                _text(item.find("pubDate")),
            )
        )
    return items


def _atom_entries(root: ET.Element) -> list[tuple[str, str, str, str, str]]:
    entries: list[tuple[str, str, str, str, str]] = []
    for entry in root.iter(f"{_ATOM_NS}entry"):
        link = ""
        for link_el in entry.findall(f"{_ATOM_NS}link"):
            rel = link_el.get("rel", "alternate")
            if rel == "alternate":
                link = link_el.get("href", "")
                break
        summary = _text(entry.find(f"{_ATOM_NS}summary")) or _text(
            entry.find(f"{_ATOM_NS}content")
        )
        date = _text(entry.find(f"{_ATOM_NS}published")) or _text(
            entry.find(f"{_ATOM_NS}updated")
        )
        entries.append(
            (
                _text(entry.find(f"{_ATOM_NS}id")) or link,
                _text(entry.find(f"{_ATOM_NS}title")),
                link,
                summary,
                date,
            )
        )
    return entries


def parse_feed(xml_text: str, *, feed_url: str) -> list[SignalCandidate]:
    """Parse an RSS 2.0 or Atom document into unscored SignalCandidates."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise RuntimeError(f"Feed {feed_url} is not valid XML: {exc}") from exc

    rows = _rss_items(root) if root.tag == "rss" else _atom_entries(root)
    host = urlsplit(feed_url).netloc.lower()
    candidates: list[SignalCandidate] = []
    for entry_id, title, link, description, date in rows:
        clean_title = _clean_html(title).strip()
        if not clean_title or not link:
            continue
        summary = trim_to_boundary(_clean_html(description), _SUMMARY_LIMIT)
        try:
            candidates.append(
                SignalCandidate(
                    source_type="rss",
                    source_id=f"{host}:{entry_id or link}",
                    title=clean_title,
                    url=link,  # type: ignore[arg-type]
                    summary=summary or clean_title,
                    raw_content=f"feed={feed_url}",
                    published_at=_parse_date(date),
                )
            )
        except Exception:
            logger.debug("Skipped malformed feed entry %r from %s.", link, feed_url)
    return candidates


async def _fetch_feed(
    client: httpx.AsyncClient, feed_url: str
) -> list[SignalCandidate]:
    response = await client.get(feed_url)
    response.raise_for_status()
    return parse_feed(response.text, feed_url=feed_url)


def _query_overlap(candidate: SignalCandidate, query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    corpus = f"{candidate.title} {candidate.summary}".lower()
    return sum(1 for token in query_tokens if token in corpus)


def _sort_key(candidate: SignalCandidate, query_tokens: set[str]) -> tuple[int, float]:
    published = candidate.published_at.timestamp() if candidate.published_at else 0.0
    return (_query_overlap(candidate, query_tokens), published)


async def fetch(query: str, *, max_results: int = 10) -> list[SignalCandidate]:
    """Fetch every configured feed and return the best `max_results` entries.

    Raises RuntimeError when no feed is configured, so discovery reports the
    source as failed instead of silently returning nothing.
    """
    feeds = settings.discovery_rss_feed_list
    if not feeds:
        raise RuntimeError("DISCOVERY_RSS_FEEDS no configurado")

    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT,
        follow_redirects=True,
    ) as client:
        results = await asyncio.gather(
            *[_fetch_feed(client, url) for url in feeds], return_exceptions=True
        )

    candidates: list[SignalCandidate] = []
    failures = 0
    for feed_url, result in zip(feeds, results, strict=True):
        if isinstance(result, BaseException):
            failures += 1
            logger.warning("RSS feed %s failed: %s", feed_url, result)
            continue
        candidates.extend(result)
    if failures == len(feeds):
        raise RuntimeError("Ningún feed RSS respondió")

    query_tokens = set(_TOKEN_RE.findall(query.lower()))
    candidates.sort(key=lambda c: _sort_key(c, query_tokens), reverse=True)
    logger.info(
        "RSS query=%r: %d feeds, %d entries, %d failed.",
        query,
        len(feeds),
        len(candidates),
        failures,
    )
    return candidates[:max_results]
