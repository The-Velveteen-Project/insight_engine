"""
Turn a pasted link into a persisted signal (Phase 3.6).

Carlos sends a URL (a blog post, a paper page, an announcement) and the
operator reads it, stores it as a signal with a substantial summary, and
offers the one-line column command. Exa contents first; a plain fetch with
HTML stripping as fallback so a missing Exa key never blocks a conversation.
"""

from __future__ import annotations

import html
import logging
import re

import aiosqlite
import httpx

from app.db.queries import insert_signal
from app.integrations import exa_client
from app.schemas.discovery import SignalCandidate
from app.utils.text import trim_to_boundary

logger = logging.getLogger(__name__)

_SUMMARY_CHARS = 1500
_RAW_CHARS = 12000
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_USER_AGENT = "VelveteenInsightEngine/0.1 (+https://github.com/The-Velveteen-Project)"


class UnreadableUrl(RuntimeError):
    """The page could not be fetched or had no readable text."""


def _strip_html(raw: str) -> tuple[str | None, str]:
    title_match = _TITLE_RE.search(raw)
    title = html.unescape(title_match.group(1)).strip() if title_match else None
    body = _SCRIPT_RE.sub(" ", raw)
    text = html.unescape(_TAG_RE.sub(" ", body))
    return title, " ".join(text.split())


async def _fetch_plain(url: str) -> tuple[str | None, str]:
    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT}, timeout=15.0, follow_redirects=True
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
    return _strip_html(response.text)


async def fetch_page(url: str) -> tuple[str, str]:
    """(title, text) for the url. Exa contents first, plain fetch second."""
    title: str | None = None
    text = ""
    try:
        results = await exa_client.get_contents([url], text_max_characters=_RAW_CHARS)
        for item in results:
            candidate_text = (item.get("text") or "").strip()
            if candidate_text:
                title = (item.get("title") or "").strip() or None
                text = candidate_text
                break
    except Exception as exc:
        logger.info(
            "Exa contents unavailable for %s (%s); fetching directly.", url, exc
        )
    if not text:
        try:
            title, text = await _fetch_plain(url)
        except Exception as exc:
            raise UnreadableUrl(f"No pude abrir {url}: {exc}") from exc
    if len(text.strip()) < 200:
        raise UnreadableUrl(f"La página {url} no tiene texto legible.")
    return (title or url), text


def _as_candidate(url: str, title: str, text: str) -> SignalCandidate:
    return SignalCandidate(
        source_type="url",
        source_id=url,
        title=trim_to_boundary(title, 200),
        url=url,  # type: ignore[arg-type]
        summary=trim_to_boundary(text, _SUMMARY_CHARS),
        raw_content=text[:_RAW_CHARS],
        relevance_score=1.0,
        relevance_note="enlace enviado por Carlos",
    )


async def read_and_store(
    db: aiosqlite.Connection, url: str, *, message_id: int | None = None
) -> tuple[int, SignalCandidate]:
    """Fetch the page, persist it as a signal, return (signal_id, candidate)."""
    title, text = await fetch_page(url)
    candidate = _as_candidate(url, title, text)
    signal_id = await insert_signal(db, candidate, message_id=message_id)
    return signal_id, candidate
