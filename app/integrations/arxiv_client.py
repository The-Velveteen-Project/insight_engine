"""
arXiv integration — Atom XML API (free, no API key, stable since 2008).

Endpoint: https://export.arxiv.org/api/query
Docs    : https://info.arxiv.org/help/api/index.html

Design choices:
- Pure XML parsing with stdlib `xml.etree.ElementTree` — no extra deps.
- `httpx.AsyncClient` with a modest timeout (10 s) and a descriptive User-Agent.
- Returns a list of `SignalCandidate` — caller owns scoring and persistence.
- Network failures propagate to the caller (discovery_service catches them).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlencode

import httpx

from app.schemas.discovery import SignalCandidate

logger = logging.getLogger(__name__)

_BASE_URL = "https://export.arxiv.org/api/query"
_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_USER_AGENT = "VelveteenInsightEngine/0.1 (https://github.com/velveteen)"
_TIMEOUT = 10.0

# arXiv date format: "2024-01-15T00:00:00Z"
_DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), _DATE_FMT)
    except ValueError:
        return None


def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _parse_feed(xml_bytes: bytes) -> list[SignalCandidate]:
    root = ET.fromstring(xml_bytes)  # noqa: S314 — trusted arXiv endpoint
    candidates: list[SignalCandidate] = []

    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        # arXiv ID lives in <id>: "http://arxiv.org/abs/2301.07041v1"
        raw_id = _text(entry.find(f"{{{_ATOM_NS}}}id"))
        # Normalise: strip version suffix, extract just the ID part
        arxiv_id = raw_id.rstrip("/").split("/abs/")[-1].split("v")[0]

        title = _text(entry.find(f"{{{_ATOM_NS}}}title")).replace("\n", " ")
        summary = _text(entry.find(f"{{{_ATOM_NS}}}summary")).replace("\n", " ")
        # Trim summary to 500 chars to keep DB rows reasonable
        if len(summary) > 500:
            summary = summary[:497] + "…"

        published = _parse_date(_text(entry.find(f"{{{_ATOM_NS}}}published")))

        # Canonical URL — prefer the abs link over the id URL
        url = f"https://arxiv.org/abs/{arxiv_id}"

        if not arxiv_id or not title:
            continue

        candidates.append(
            SignalCandidate(
                source_type="arxiv",
                source_id=arxiv_id,
                title=title,
                url=url,  # type: ignore[arg-type]
                summary=summary,
                raw_content=xml_bytes.decode(errors="replace")[:2000],
                published_at=published,
            )
        )

    return candidates


async def fetch(query: str, *, max_results: int = 10) -> list[SignalCandidate]:
    """
    Query the arXiv API and return parsed SignalCandidates (unscored).

    Args:
        query       : free-text search string (arXiv supports AND/OR operators)
        max_results : number of entries to retrieve (default 10, max 100)
    """
    params = urlencode(
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "lastUpdatedDate",
            "sortOrder": "descending",
        }
    )
    url = f"{_BASE_URL}?{params}"

    transport = httpx.AsyncHTTPTransport(retries=2)
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT,
        transport=transport,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

    candidates = _parse_feed(response.content)
    logger.info(
        "arXiv query=%r returned %d entries, parsed %d candidates.",
        query,
        max_results,
        len(candidates),
    )
    return candidates
