"""
Direct job-board readers for the companies the goal names.

Anthropic and Google DeepMind publish through Greenhouse, OpenAI through
Ashby. Both expose public, unauthenticated JSON endpoints, so the radar
can read the boards themselves instead of hoping Exa indexed the posting.

Greenhouse : GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs
             GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}
Ashby      : GET https://api.ashbyhq.com/posting-api/job-board/{board}

Behavior:
- The Greenhouse list carries no description; `fetch_greenhouse_content`
  fetches the body only for the postings the radar has not seen before.
- Ashby returns every description in one payload (~13 MB for OpenAI), so a
  single call is enough.
- Network failures propagate. The radar reports each board as its own
  outcome, so one failed board never hides the others.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

_GREENHOUSE_BASE = "https://boards-api.greenhouse.io/v1/boards"
_ASHBY_BASE = "https://api.ashbyhq.com/posting-api/job-board"
_USER_AGENT = "VelveteenInsightEngine/0.1 (https://github.com/velveteen)"
_TIMEOUT = 20.0

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)[^>]*>", re.IGNORECASE)
_BLANK_RE = re.compile(r"[ \t ]+")
_NEWLINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class BoardPosting:
    board: str  # "greenhouse" or "ashby"
    board_slug: str  # e.g. "anthropic", "openai"
    source_id: str
    title: str
    url: str
    location: str | None
    remote: bool | None
    department: str | None
    published_at: datetime | None
    # Plain text of the posting when the board returns it in the listing
    # (Ashby). Empty for Greenhouse until fetched per job.
    text: str
    salary_min_usd_year: float | None = None
    salary_max_usd_year: float | None = None


def html_to_text(raw: str) -> str:
    """Greenhouse escapes the HTML twice; Ashby sends it plain. Both end up
    as readable text with paragraph breaks preserved."""
    unescaped = html.unescape(html.unescape(raw or ""))
    with_breaks = _BLOCK_RE.sub("\n", unescaped)
    stripped = _TAG_RE.sub(" ", with_breaks)
    lines = [_BLANK_RE.sub(" ", line).strip() for line in stripped.split("\n")]
    return _NEWLINES_RE.sub("\n\n", "\n".join(lines)).strip()


def _parse_stamp(raw: object) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _looks_remote(*parts: str | None) -> bool | None:
    corpus = " ".join(p for p in parts if p).lower()
    if not corpus:
        return None
    return True if "remote" in corpus else None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        timeout=_TIMEOUT,
        transport=httpx.AsyncHTTPTransport(retries=2),
    )


def parse_greenhouse_jobs(payload: object, *, board_slug: str) -> list[BoardPosting]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise RuntimeError("Unexpected Greenhouse payload: expected 'jobs' list.")
    postings: list[BoardPosting] = []
    for item in payload["jobs"]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("absolute_url") or "").strip()
        job_id = item.get("id")
        if not title or not url or job_id is None:
            continue
        location_obj = item.get("location")
        location = (
            str(location_obj.get("name") or "").strip() or None
            if isinstance(location_obj, dict)
            else None
        )
        departments = item.get("departments")
        department = None
        if isinstance(departments, list) and departments:
            first = departments[0]
            if isinstance(first, dict) and first.get("name"):
                department = str(first["name"]).strip()
        postings.append(
            BoardPosting(
                board="greenhouse",
                board_slug=board_slug,
                source_id=str(job_id),
                title=title,
                url=url,
                location=location,
                remote=_looks_remote(location),
                department=department,
                published_at=_parse_stamp(item.get("first_published")),
                text=html_to_text(str(item.get("content") or "")),
            )
        )
    return postings


def parse_ashby_jobs(payload: object, *, board_slug: str) -> list[BoardPosting]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise RuntimeError("Unexpected Ashby payload: expected 'jobs' list.")
    postings: list[BoardPosting] = []
    for item in payload["jobs"]:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("jobUrl") or "").strip()
        job_id = str(item.get("id") or "").strip()
        if not title or not url or not job_id:
            continue
        locations = [str(item.get("location") or "").strip()]
        secondary = item.get("secondaryLocations")
        if isinstance(secondary, list):
            for extra in secondary:
                if isinstance(extra, dict) and extra.get("location"):
                    locations.append(str(extra["location"]).strip())
        location = "; ".join(loc for loc in locations if loc) or None
        remote = True if item.get("isRemote") else _looks_remote(location)
        text = str(item.get("descriptionPlain") or "").strip()
        if not text:
            text = html_to_text(str(item.get("descriptionHtml") or ""))
        salary_min, salary_max = _ashby_salary(item.get("compensation"))
        postings.append(
            BoardPosting(
                board="ashby",
                board_slug=board_slug,
                source_id=job_id,
                title=title,
                url=url,
                location=location,
                remote=remote,
                department=str(item.get("department") or "").strip() or None,
                published_at=_parse_stamp(item.get("publishedAt")),
                text=text,
                salary_min_usd_year=salary_min,
                salary_max_usd_year=salary_max,
            )
        )
    return postings


def _ashby_salary(compensation: object) -> tuple[float | None, float | None]:
    """Yearly USD salary from Ashby's compensation block, when stated."""
    if not isinstance(compensation, dict):
        return None, None
    components = compensation.get("summaryComponents")
    if not isinstance(components, list):
        return None, None
    for component in components:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") != "Salary":
            continue
        if component.get("currencyCode") != "USD":
            continue
        if str(component.get("interval") or "").upper() != "1 YEAR":
            continue
        low = component.get("minValue")
        high = component.get("maxValue")
        return (
            float(low) if isinstance(low, int | float) else None,
            float(high) if isinstance(high, int | float) else None,
        )
    return None, None


async def fetch_greenhouse(board_slug: str) -> list[BoardPosting]:
    """Every open posting on a Greenhouse board, without descriptions."""
    async with _client() as client:
        response = await client.get(f"{_GREENHOUSE_BASE}/{board_slug}/jobs")
        response.raise_for_status()
    return parse_greenhouse_jobs(response.json(), board_slug=board_slug)


async def fetch_greenhouse_content(board_slug: str, job_id: str) -> str:
    """Plain text of one Greenhouse posting. Empty string when unavailable."""
    async with _client() as client:
        response = await client.get(f"{_GREENHOUSE_BASE}/{board_slug}/jobs/{job_id}")
        response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return ""
    return html_to_text(str(payload.get("content") or ""))


async def fetch_ashby(board_slug: str) -> list[BoardPosting]:
    """Every listed posting on an Ashby board, descriptions included."""
    async with _client() as client:
        response = await client.get(
            f"{_ASHBY_BASE}/{board_slug}", params={"includeCompensation": "true"}
        )
        response.raise_for_status()
    return parse_ashby_jobs(response.json(), board_slug=board_slug)
