"""
GitHub Insight Service — detect portfolio signals from public repositories.

Phase 5 intentionally keeps this deterministic and modest:
- repo metadata
- README
- recent commits as weak evidence of activity
- root contents and a small key-file allowlist

No drafts, no LLMs, no recursive crawling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import aiosqlite

from app.core.config import settings
from app.db.queries import insert_signal
from app.integrations import github_client
from app.schemas.discovery import SignalCandidate
from app.schemas.github import (
    GitHubCommitSummary,
    GitHubContentEntry,
    GitHubInsightCandidate,
    GitHubRepoMetadata,
    GitHubTextFile,
)

logger = logging.getLogger(__name__)

_KEY_TEXT_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Dockerfile",
    "Makefile",
)
_RECENT_COMMIT_WINDOW = timedelta(days=30)
_RAW_CONTENT_LIMIT = 1800
_SUMMARY_LIMIT = 420
_KEYWORD_INCREMENT = 0.12
_KEYWORD_CAP = 0.48
_RECENCY_BONUS = 0.12
_README_BONUS = 0.08
_STRUCTURE_BONUS = 0.08
_ARTIFACT_BONUS = 0.10
_ACTIVITY_BONUS = 0.08
_MAX_SCORE = 1.0
_KEYWORDS = [
    "agent",
    "agentic",
    "ai",
    "analytics",
    "applied",
    "bayesian",
    "climate",
    "decision",
    "education",
    "health",
    "llm",
    "machine learning",
    "model",
    "nlp",
    "portfolio",
    "research",
    "risk",
    "software",
    "system",
    "workflow",
]
_STACK_PATTERNS = {
    "fastapi": re.compile(r"\bfastapi\b", re.IGNORECASE),
    "pydantic": re.compile(r"\bpydantic\b", re.IGNORECASE),
    "pytest": re.compile(r"\bpytest\b", re.IGNORECASE),
    "ruff": re.compile(r"\bruff\b", re.IGNORECASE),
    "mypy": re.compile(r"\bmypy\b", re.IGNORECASE),
    "httpx": re.compile(r"\bhttpx\b", re.IGNORECASE),
}


def _limit_text(text: str, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 14]}...[truncated]"


def _repo_source_id(
    repo_full_name: str,
    insight_type: str,
    qualifier: str | None = None,
) -> str:
    """
    Stable source_id convention for persisted GitHub insights.

    Examples:
    - the-velveteen-project/stochastogreen::overview
    - the-velveteen-project/ecoagent::activity
    - the-velveteen-project/ecoagent::artifact::pyproject.toml
    """
    base = repo_full_name.strip().lower()
    if qualifier is None:
        return f"{base}::{insight_type}"
    normalized = qualifier.strip().lower()
    return f"{base}::{insight_type}::{normalized}"


def _commit_subject(commit: GitHubCommitSummary) -> str:
    return _limit_text(commit.message.splitlines()[0], 90)


def _recent_commit_count(commits: Sequence[GitHubCommitSummary]) -> int:
    now = datetime.now(UTC)
    count = 0
    for commit in commits:
        committed_at = commit.committed_at
        if committed_at is None:
            continue
        if committed_at.tzinfo is None:
            committed_at = committed_at.replace(tzinfo=UTC)
        if now - committed_at <= _RECENT_COMMIT_WINDOW:
            count += 1
    return count


def _collect_stack_signals(text: str) -> list[str]:
    matches: list[str] = []
    for name, pattern in _STACK_PATTERNS.items():
        if pattern.search(text):
            matches.append(name)
    return matches


def _build_repo_corpus(
    metadata: GitHubRepoMetadata,
    readme: GitHubTextFile | None,
    contents: Sequence[GitHubContentEntry],
) -> str:
    parts = [
        metadata.full_name,
        metadata.description or "",
        " ".join(metadata.topics),
        " ".join(entry.name for entry in contents),
    ]
    if readme is not None:
        parts.append(readme.text)
    return " ".join(parts).lower()


def _keyword_hits(corpus: str) -> list[str]:
    hits: list[str] = []
    for keyword in _KEYWORDS:
        if keyword in corpus:
            hits.append(keyword)
    return hits


def _score_candidate(
    candidate: GitHubInsightCandidate,
    *,
    keyword_hits: list[str],
    has_readme: bool,
    structure_count: int,
    recent_commit_count: int,
) -> tuple[float, str]:
    keyword_score = min(len(keyword_hits) * _KEYWORD_INCREMENT, _KEYWORD_CAP)
    score = keyword_score
    notes: list[str] = []

    if keyword_hits:
        notes.append(f"keywords={keyword_hits[:4]}")
    if has_readme:
        score += _README_BONUS
        notes.append("readme")
    if structure_count >= 2:
        score += _STRUCTURE_BONUS
        notes.append(f"structure={structure_count}")

    if candidate.insight_type == "artifact":
        score += _ARTIFACT_BONUS
        notes.append("artifact")

    if candidate.insight_type == "activity" and recent_commit_count > 0:
        score += _ACTIVITY_BONUS
        notes.append(f"recent_commits={recent_commit_count}")

    published_at = candidate.published_at
    if published_at is not None:
        normalized = published_at
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=UTC)
        if datetime.now(UTC) - normalized <= _RECENT_COMMIT_WINDOW:
            score += _RECENCY_BONUS
            notes.append("recent")

    return round(min(score, _MAX_SCORE), 4), "; ".join(notes) or "weak repo signal"


def _to_signal_candidate(candidate: GitHubInsightCandidate) -> SignalCandidate:
    return SignalCandidate(
        source_type="github",
        source_id=candidate.source_id,
        title=candidate.title,
        url=str(candidate.url),  # type: ignore[arg-type]
        summary=candidate.summary,
        raw_content=candidate.raw_content,
        relevance_score=candidate.relevance_score,
        relevance_note=candidate.relevance_note,
        published_at=candidate.published_at,
    )


async def _safe_repo_fetch(
    repo_full_name: str,
) -> tuple[
    GitHubRepoMetadata | None,
    GitHubTextFile | None,
    list[GitHubCommitSummary],
    list[GitHubContentEntry],
]:
    try:
        metadata = await github_client.fetch_repo_metadata(repo_full_name)
    except Exception as exc:
        logger.warning("GitHub repo fetch failed for %r: %s", repo_full_name, exc)
        return None, None, [], []

    readme_task = github_client.fetch_readme(repo_full_name)
    commits_task = github_client.fetch_recent_commits(
        repo_full_name,
        per_page=settings.github_commits_limit,
    )
    contents_task = github_client.fetch_root_contents(repo_full_name)
    readme, commits, contents = await asyncio.gather(
        readme_task,
        commits_task,
        contents_task,
        return_exceptions=True,
    )

    safe_readme = (
        readme if isinstance(readme, GitHubTextFile) or readme is None else None
    )
    safe_commits = commits if isinstance(commits, list) else []
    safe_contents = contents if isinstance(contents, list) else []

    if isinstance(readme, Exception):
        logger.warning("GitHub README fetch failed for %r: %s", repo_full_name, readme)
    if isinstance(commits, Exception):
        logger.warning(
            "GitHub commits fetch failed for %r: %s", repo_full_name, commits
        )
    if isinstance(contents, Exception):
        logger.warning(
            "GitHub contents fetch failed for %r: %s", repo_full_name, contents
        )

    return metadata, safe_readme, safe_commits, safe_contents


async def _fetch_key_text_files(
    repo_full_name: str,
    contents: Sequence[GitHubContentEntry],
) -> dict[str, GitHubTextFile]:
    tasks: dict[str, asyncio.Task[GitHubTextFile | None]] = {}
    root_paths = {entry.path for entry in contents if entry.type == "file"}
    for path in _KEY_TEXT_FILES:
        if path in root_paths:
            tasks[path] = asyncio.create_task(
                github_client.fetch_text_file(repo_full_name, path)
            )

    key_files: dict[str, GitHubTextFile] = {}
    for path, task in tasks.items():
        try:
            document = await task
        except Exception as exc:
            logger.warning(
                "GitHub file fetch failed for %r %r: %s", repo_full_name, path, exc
            )
            continue
        if document is not None:
            key_files[path] = document
    return key_files


def _overview_candidate(
    metadata: GitHubRepoMetadata,
    readme: GitHubTextFile | None,
    contents: Sequence[GitHubContentEntry],
) -> GitHubInsightCandidate:
    root_items = [entry.name for entry in contents[:8]]
    evidence = [
        f"language={metadata.language or 'unknown'}",
        f"topics={', '.join(metadata.topics[:4]) or 'none'}",
        f"root={', '.join(root_items) or 'empty'}",
    ]
    if readme is not None and readme.text:
        evidence.append(f"readme={_limit_text(readme.text, 100)}")

    summary = (
        f"Tu repo {metadata.full_name} muestra una superficie de portafolio "
        "reutilizable: framing público, estructura de raíz y documentación "
        "visible. Te sirve como pieza de credibilidad técnica antes que como "
        "evidencia de impacto."
    )
    raw = json.dumps(
        {
            "repo": metadata.full_name,
            "description": metadata.description,
            "topics": metadata.topics[:6],
            "root_items": root_items,
            "readme_excerpt": readme.text[:240] if readme is not None else "",
        }
    )
    return GitHubInsightCandidate(
        repo_full_name=metadata.full_name,
        insight_type="overview",
        source_id=_repo_source_id(metadata.full_name, "overview"),
        title=f"{metadata.full_name}: overview signal",
        url=metadata.html_url,
        summary=_limit_text(summary, _SUMMARY_LIMIT),
        evidence=evidence,
        raw_content=_limit_text(raw, _RAW_CONTENT_LIMIT),
        published_at=metadata.updated_at,
    )


def _activity_candidate(
    metadata: GitHubRepoMetadata,
    commits: Sequence[GitHubCommitSummary],
) -> GitHubInsightCandidate | None:
    if not commits:
        return None
    subjects = [_commit_subject(commit) for commit in commits[:4]]
    latest_commit_at = commits[0].committed_at
    summary = (
        f"Tu repo {metadata.full_name} muestra actividad reciente; lee como "
        "señal débil de cadencia, no como evidencia de impacto. Te sirve "
        "para verificar que la línea de trabajo sigue viva."
    )
    raw = json.dumps(
        {
            "repo": metadata.full_name,
            "commit_subjects": subjects,
        }
    )
    return GitHubInsightCandidate(
        repo_full_name=metadata.full_name,
        insight_type="activity",
        source_id=_repo_source_id(metadata.full_name, "activity"),
        title=f"{metadata.full_name}: activity signal",
        url=commits[0].html_url,
        summary=_limit_text(summary, _SUMMARY_LIMIT),
        evidence=[f"commit={subject}" for subject in subjects],
        raw_content=_limit_text(raw, _RAW_CONTENT_LIMIT),
        published_at=latest_commit_at,
    )


def _artifact_candidates(
    metadata: GitHubRepoMetadata,
    contents: Sequence[GitHubContentEntry],
    key_files: dict[str, GitHubTextFile],
) -> list[GitHubInsightCandidate]:
    candidates: list[GitHubInsightCandidate] = []
    dirs = {entry.name for entry in contents if entry.type == "dir"}

    if "tests" in dirs:
        raw = json.dumps({"repo": metadata.full_name, "artifact": "tests"})
        candidates.append(
            GitHubInsightCandidate(
                repo_full_name=metadata.full_name,
                insight_type="artifact",
                source_id=_repo_source_id(metadata.full_name, "artifact", "tests"),
                title=f"{metadata.full_name}: testing surface",
                url=metadata.html_url,
                summary=(
                    f"Tu repo {metadata.full_name} expone un directorio "
                    "`tests/` en la raíz. Te sirve como señal concreta de "
                    "disciplina de ingeniería: separa proyecto demo de código "
                    "que un cliente puede auditar."
                ),
                evidence=["root_dir=tests"],
                raw_content=_limit_text(raw, _RAW_CONTENT_LIMIT),
                published_at=metadata.updated_at,
            )
        )

    for path, document in key_files.items():
        stack = _collect_stack_signals(document.text)
        evidence = [f"path={path}"]
        if stack:
            evidence.append(f"stack={', '.join(stack[:4])}")
        if document.truncated:
            evidence.append("content=truncated")

        summary = (
            f"Tu repo {metadata.full_name} expone un artefacto de build en "
            f"`{path}`. Te sirve como señal de stack y forma de entrega "
            "visible sin abrir el código; no afirma nada sobre la calidad "
            "del producto."
        )
        raw = json.dumps(
            {
                "repo": metadata.full_name,
                "path": path,
                "excerpt": document.text[:240],
                "stack": stack[:6],
            }
        )
        target_url = document.html_url or metadata.html_url
        candidates.append(
            GitHubInsightCandidate(
                repo_full_name=metadata.full_name,
                insight_type="artifact",
                source_id=_repo_source_id(metadata.full_name, "artifact", path),
                title=f"{metadata.full_name}: artifact signal from {path}",
                url=target_url,
                summary=_limit_text(summary, _SUMMARY_LIMIT),
                evidence=evidence,
                raw_content=_limit_text(raw, _RAW_CONTENT_LIMIT),
                published_at=metadata.updated_at,
            )
        )

    return candidates


def _score_repo_candidates(
    candidates: Sequence[GitHubInsightCandidate],
    *,
    metadata: GitHubRepoMetadata,
    readme: GitHubTextFile | None,
    contents: Sequence[GitHubContentEntry],
    commits: Sequence[GitHubCommitSummary],
) -> list[GitHubInsightCandidate]:
    corpus = _build_repo_corpus(metadata, readme, contents)
    keyword_hits = _keyword_hits(corpus)
    has_readme = readme is not None and bool(readme.text)
    structure_count = len(
        [
            entry
            for entry in contents
            if entry.name in {"app", "src", "tests", "docs", "notebooks", "scripts"}
        ]
    )
    recent_commits = _recent_commit_count(commits)

    scored: list[GitHubInsightCandidate] = []
    for candidate in candidates:
        score, note = _score_candidate(
            candidate,
            keyword_hits=keyword_hits,
            has_readme=has_readme,
            structure_count=structure_count,
            recent_commit_count=recent_commits,
        )
        scored.append(
            candidate.model_copy(
                update={"relevance_score": score, "relevance_note": note}
            )
        )
    return sorted(scored, key=lambda item: item.relevance_score, reverse=True)


async def suggest_repo_insights(
    repos: Sequence[str],
    db: aiosqlite.Connection,
    *,
    limit: int | None = None,
    message_id: int | None = None,
) -> list[GitHubInsightCandidate]:
    resolved_limit = limit or settings.github_insights_default_limit
    collected: list[GitHubInsightCandidate] = []

    for repo_full_name in repos:
        metadata, readme, commits, contents = await _safe_repo_fetch(repo_full_name)
        if metadata is None:
            continue

        key_files = await _fetch_key_text_files(metadata.full_name, contents)
        repo_candidates: list[GitHubInsightCandidate] = [
            _overview_candidate(metadata, readme, contents)
        ]
        activity = _activity_candidate(metadata, commits)
        if activity is not None:
            repo_candidates.append(activity)
        repo_candidates.extend(_artifact_candidates(metadata, contents, key_files))
        collected.extend(
            _score_repo_candidates(
                repo_candidates,
                metadata=metadata,
                readme=readme,
                contents=contents,
                commits=commits,
            )
        )

    top = sorted(collected, key=lambda item: item.relevance_score, reverse=True)[
        :resolved_limit
    ]

    for candidate in top:
        try:
            await insert_signal(
                db,
                _to_signal_candidate(candidate),
                message_id=message_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist GitHub insight source_id=%r: %s",
                candidate.source_id,
                exc,
            )

    logger.info(
        "GitHub insight complete: repos=%d candidates=%d returned=%d.",
        len(repos),
        len(collected),
        len(top),
    )
    return top
