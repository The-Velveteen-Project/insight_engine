"""
GitHub public REST client for Phase 5.

Scope is intentionally narrow:
- repo metadata
- README
- recent commits
- root contents
- a small allowlist of key text files

No cloning, no HTML scraping, no recursive crawling.
"""

from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime
from typing import Any, cast

import httpx

from app.core.config import settings
from app.schemas.github import (
    GitHubCommitSummary,
    GitHubContentEntry,
    GitHubRepoMetadata,
    GitHubTextFile,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.github.com"
_TIMEOUT = 15.0
_USER_AGENT = "VelveteenInsightEngine/0.1"
_TEXT_FILE_MAX_BYTES = 12_000


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _parse_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _limit_text(text: str, max_chars: int) -> tuple[str, bool]:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned, False
    return f"{cleaned[: max_chars - 14]}...[truncated]", True


def _decode_base64_text(
    encoded: str | None,
    *,
    max_bytes: int = _TEXT_FILE_MAX_BYTES,
) -> tuple[str, bool]:
    if not encoded:
        return "", False
    try:
        data = base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError):
        logger.warning("Failed to decode GitHub base64 content.")
        return "", False
    truncated = len(data) > max_bytes
    trimmed = data[:max_bytes]
    text = trimmed.decode("utf-8", errors="replace")
    if truncated:
        text = f"{text}\n...[truncated]"
    return text, truncated


def _parse_repo_metadata(payload: dict[str, Any]) -> GitHubRepoMetadata:
    owner = payload.get("owner")
    owner_login = owner.get("login", "") if isinstance(owner, dict) else ""
    return GitHubRepoMetadata(
        full_name=str(payload.get("full_name", "")),
        name=str(payload.get("name", "")),
        owner_login=str(owner_login),
        description=cast(str | None, payload.get("description")),
        html_url=str(payload.get("html_url", "")),
        topics=cast(list[str], payload.get("topics", [])),
        language=cast(str | None, payload.get("language")),
        stargazers_count=int(payload.get("stargazers_count", 0) or 0),
        default_branch=str(payload.get("default_branch", "main")),
        archived=bool(payload.get("archived", False)),
        updated_at=_parse_datetime(cast(str | None, payload.get("updated_at"))),
    )


def _parse_commits(payload: list[dict[str, Any]]) -> list[GitHubCommitSummary]:
    commits: list[GitHubCommitSummary] = []
    for item in payload:
        commit = item.get("commit")
        if not isinstance(commit, dict):
            continue
        author = commit.get("author")
        committed_at = None
        if isinstance(author, dict):
            committed_at = _parse_datetime(cast(str | None, author.get("date")))
        message = str(commit.get("message", "")).strip()
        sha = str(item.get("sha", ""))
        html_url = str(item.get("html_url", ""))
        if not sha or not message or not html_url:
            continue
        commits.append(
            GitHubCommitSummary(
                sha=sha,
                message=message,
                html_url=html_url,
                committed_at=committed_at,
            )
        )
    return commits


def _parse_root_contents(payload: list[dict[str, Any]]) -> list[GitHubContentEntry]:
    contents: list[GitHubContentEntry] = []
    for item in payload:
        item_type = item.get("type")
        if item_type not in {"file", "dir"}:
            continue
        path = str(item.get("path", ""))
        if not path or "/" in path:
            continue
        html_url = item.get("html_url")
        contents.append(
            GitHubContentEntry(
                name=str(item.get("name", "")),
                path=path,
                type=item_type,
                size=int(item.get("size", 0) or 0),
                html_url=str(html_url)
                if isinstance(html_url, str) and html_url
                else None,
            )
        )
    return contents


def _parse_text_file(
    payload: dict[str, Any], *, max_chars: int
) -> GitHubTextFile | None:
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        return None
    decoded, truncated = _decode_base64_text(cast(str | None, payload.get("content")))
    limited, limited_truncated = _limit_text(decoded, max_chars)
    html_url = payload.get("html_url")
    return GitHubTextFile(
        path=path,
        html_url=str(html_url) if isinstance(html_url, str) and html_url else None,
        text=limited,
        truncated=truncated or limited_truncated,
    )


async def _get_json(path: str, *, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        headers=_headers(),
        timeout=_TIMEOUT,
    ) as client:
        response = await client.get(path, params=params)
        response.raise_for_status()
    return response.json()


async def fetch_repo_metadata(repo_full_name: str) -> GitHubRepoMetadata:
    payload = await _get_json(f"/repos/{repo_full_name}")
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub repo payload.")
    return _parse_repo_metadata(payload)


async def fetch_readme(
    repo_full_name: str,
    *,
    max_chars: int = 4000,
) -> GitHubTextFile | None:
    try:
        payload = await _get_json(f"/repos/{repo_full_name}/readme")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub README payload.")
    return _parse_text_file(payload, max_chars=max_chars)


async def fetch_recent_commits(
    repo_full_name: str,
    *,
    per_page: int,
) -> list[GitHubCommitSummary]:
    payload = await _get_json(
        f"/repos/{repo_full_name}/commits",
        params={"per_page": per_page},
    )
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected GitHub commits payload.")
    dict_payload = [item for item in payload if isinstance(item, dict)]
    return _parse_commits(cast(list[dict[str, Any]], dict_payload))


async def fetch_root_contents(repo_full_name: str) -> list[GitHubContentEntry]:
    payload = await _get_json(f"/repos/{repo_full_name}/contents")
    if not isinstance(payload, list):
        raise RuntimeError("Unexpected GitHub contents payload.")
    dict_payload = [item for item in payload if isinstance(item, dict)]
    return _parse_root_contents(cast(list[dict[str, Any]], dict_payload))


async def fetch_text_file(
    repo_full_name: str,
    path: str,
    *,
    max_chars: int = 2000,
) -> GitHubTextFile | None:
    try:
        payload = await _get_json(f"/repos/{repo_full_name}/contents/{path}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected GitHub file payload.")
    return _parse_text_file(payload, max_chars=max_chars)
