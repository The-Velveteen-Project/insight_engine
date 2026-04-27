"""
Tests for GitHub Insight Service.

All network-dependent calls are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite

from app.integrations.github_client import (
    _decode_base64_text,
    _parse_commits,
    _parse_repo_metadata,
)
from app.schemas.github import (
    GitHubCommitSummary,
    GitHubContentEntry,
    GitHubInsightCandidate,
    GitHubRepoMetadata,
    GitHubTextFile,
)
from app.services.github_insight_service import _repo_source_id, suggest_repo_insights

_NOW = datetime.now(UTC)
_RECENT = _NOW - timedelta(days=3)


def _metadata(
    full_name: str = "The-Velveteen-Project/StochastoGreen",
) -> GitHubRepoMetadata:
    return GitHubRepoMetadata(
        full_name=full_name,
        name=full_name.split("/")[-1],
        owner_login=full_name.split("/")[0],
        description="Applied climate risk and agent workflows for resilient systems.",
        html_url=f"https://github.com/{full_name}",  # type: ignore[arg-type]
        topics=["climate", "agents", "applied-research"],
        language="Python",
        stargazers_count=5,
        default_branch="main",
        archived=False,
        updated_at=_RECENT,
    )


def _commits() -> list[GitHubCommitSummary]:
    return [
        GitHubCommitSummary(
            sha="abc123",
            message="Add FastAPI endpoint for simulation status",
            html_url="https://github.com/The-Velveteen-Project/StochastoGreen/commit/abc123",  # type: ignore[arg-type]
            committed_at=_RECENT,
        ),
        GitHubCommitSummary(
            sha="def456",
            message="Refine tests for climate scenario pipeline",
            html_url="https://github.com/The-Velveteen-Project/StochastoGreen/commit/def456",  # type: ignore[arg-type]
            committed_at=_RECENT,
        ),
    ]


def _contents() -> list[GitHubContentEntry]:
    return [
        GitHubContentEntry(
            name="README.md",
            path="README.md",
            type="file",
            size=1200,
            html_url="https://github.com/The-Velveteen-Project/StochastoGreen/blob/main/README.md",  # type: ignore[arg-type]
        ),
        GitHubContentEntry(
            name="pyproject.toml",
            path="pyproject.toml",
            type="file",
            size=300,
            html_url="https://github.com/The-Velveteen-Project/StochastoGreen/blob/main/pyproject.toml",  # type: ignore[arg-type]
        ),
        GitHubContentEntry(
            name="tests",
            path="tests",
            type="dir",
            size=0,
            html_url="https://github.com/The-Velveteen-Project/StochastoGreen/tree/main/tests",  # type: ignore[arg-type]
        ),
    ]


def _readme() -> GitHubTextFile:
    return GitHubTextFile(
        path="README.md",
        html_url="https://github.com/The-Velveteen-Project/StochastoGreen/blob/main/README.md",  # type: ignore[arg-type]
        text=(
            "StochastoGreen explores climate risk, stochastic modeling, "
            "decision support, and agent workflows for applied research."
        ),
        truncated=False,
    )


def _pyproject() -> GitHubTextFile:
    return GitHubTextFile(
        path="pyproject.toml",
        html_url="https://github.com/The-Velveteen-Project/StochastoGreen/blob/main/pyproject.toml",  # type: ignore[arg-type]
        text='dependencies = ["fastapi", "pydantic", "pytest", "ruff", "mypy"]',
        truncated=False,
    )


def test_repo_source_id_convention_is_stable() -> None:
    assert (
        _repo_source_id("The-Velveteen-Project/EcoAgent", "overview")
        == "the-velveteen-project/ecoagent::overview"
    )
    assert (
        _repo_source_id("The-Velveteen-Project/EcoAgent", "artifact", "pyproject.toml")
        == "the-velveteen-project/ecoagent::artifact::pyproject.toml"
    )


def test_decode_base64_text_truncates_safely() -> None:
    import base64 as _b64

    encoded = _b64.b64encode(b"a" * 8000).decode()
    text, truncated = _decode_base64_text(encoded, max_bytes=10)
    assert truncated is True
    assert "[truncated]" in text


def test_parse_repo_metadata() -> None:
    payload = {
        "full_name": "The-Velveteen-Project/EcoAgent",
        "name": "EcoAgent",
        "owner": {"login": "The-Velveteen-Project"},
        "description": "Agentic sustainability lab",
        "html_url": "https://github.com/The-Velveteen-Project/EcoAgent",
        "topics": ["agents", "climate"],
        "language": "Python",
        "stargazers_count": 10,
        "default_branch": "main",
        "archived": False,
        "updated_at": "2026-04-19T12:00:00Z",
    }
    metadata = _parse_repo_metadata(payload)
    assert metadata.full_name == "The-Velveteen-Project/EcoAgent"
    assert metadata.owner_login == "The-Velveteen-Project"
    assert metadata.updated_at is not None
    assert metadata.updated_at.year == 2026


def test_parse_commits() -> None:
    payload = [
        {
            "sha": "abc",
            "html_url": "https://github.com/example/repo/commit/abc",
            "commit": {
                "message": "Add health modeling workflow",
                "author": {"date": "2026-04-18T10:00:00Z"},
            },
        }
    ]
    commits = _parse_commits(payload)
    assert len(commits) == 1
    assert commits[0].message == "Add health modeling workflow"


async def test_suggest_repo_insights_persists_candidates(
    db: aiosqlite.Connection,
) -> None:
    before_cursor = await db.execute(
        "SELECT COUNT(*) FROM signals WHERE source_type = 'github'"
    )
    before_row = await before_cursor.fetchone()
    assert before_row is not None
    before_count = int(before_row[0])

    with (
        patch(
            "app.services.github_insight_service.github_client.fetch_repo_metadata",
            new=AsyncMock(return_value=_metadata()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_readme",
            new=AsyncMock(return_value=_readme()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_recent_commits",
            new=AsyncMock(return_value=_commits()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_root_contents",
            new=AsyncMock(return_value=_contents()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_text_file",
            new=AsyncMock(return_value=_pyproject()),
        ),
    ):
        insights = await suggest_repo_insights(
            ["The-Velveteen-Project/StochastoGreen"],
            db,
            limit=4,
        )

    assert len(insights) >= 3
    assert all(item.relevance_score > 0.0 for item in insights)

    cursor = await db.execute(
        "SELECT COUNT(*) FROM signals WHERE source_type = 'github'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] - before_count == len(insights)


async def test_suggest_repo_insights_refreshes_existing_signal(
    db: aiosqlite.Connection,
) -> None:
    with (
        patch(
            "app.services.github_insight_service.github_client.fetch_repo_metadata",
            new=AsyncMock(return_value=_metadata()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_readme",
            new=AsyncMock(return_value=_readme()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_recent_commits",
            new=AsyncMock(return_value=_commits()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_root_contents",
            new=AsyncMock(return_value=_contents()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_text_file",
            new=AsyncMock(return_value=_pyproject()),
        ),
    ):
        await suggest_repo_insights(
            ["The-Velveteen-Project/StochastoGreen"], db, limit=2
        )
        await suggest_repo_insights(
            ["The-Velveteen-Project/StochastoGreen"], db, limit=2
        )

    cursor = await db.execute(
        """
        SELECT COUNT(*) AS total
        FROM signals
        WHERE source_type = 'github'
          AND source_id = 'the-velveteen-project/stochastogreen::overview'
        """
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["total"] == 1


async def test_suggest_repo_insights_isolates_repo_failure(
    db: aiosqlite.Connection,
) -> None:
    async def fetch_repo_metadata(repo_full_name: str) -> GitHubRepoMetadata:
        if repo_full_name.endswith("StochastoGreen"):
            raise RuntimeError("boom")
        return _metadata("The-Velveteen-Project/EcoAgent")

    with (
        patch(
            "app.services.github_insight_service.github_client.fetch_repo_metadata",
            new=AsyncMock(side_effect=fetch_repo_metadata),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_readme",
            new=AsyncMock(return_value=_readme()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_recent_commits",
            new=AsyncMock(return_value=_commits()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_root_contents",
            new=AsyncMock(return_value=_contents()),
        ),
        patch(
            "app.services.github_insight_service.github_client.fetch_text_file",
            new=AsyncMock(return_value=_pyproject()),
        ),
    ):
        insights = await suggest_repo_insights(
            [
                "The-Velveteen-Project/StochastoGreen",
                "The-Velveteen-Project/EcoAgent",
            ],
            db,
            limit=3,
        )

    assert insights
    assert all(item.repo_full_name.endswith("EcoAgent") for item in insights)


async def test_github_route_returns_200(client) -> None:
    with patch(
        "app.api.routes.github.github_insight_service.suggest_repo_insights",
        new=AsyncMock(
            return_value=[
                GitHubInsightCandidate(
                    repo_full_name="The-Velveteen-Project/StochastoGreen",
                    insight_type="overview",
                    source_id="the-velveteen-project/stochastogreen::overview",
                    title="StochastoGreen: overview signal",
                    url="https://github.com/The-Velveteen-Project/StochastoGreen",  # type: ignore[arg-type]
                    summary="Repo framing suggests a reusable portfolio signal.",
                    evidence=["topics=climate, agents"],
                    relevance_score=0.72,
                    relevance_note="keywords=['climate', 'agent']",
                )
            ]
        ),
    ):
        response = await client.get(
            "/api/v1/github/insights/suggest",
            params={"repo": "The-Velveteen-Project/StochastoGreen", "limit": 1},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total_candidates"] == 1
    assert (
        body["insights"][0]["source_id"]
        == "the-velveteen-project/stochastogreen::overview"
    )


async def test_github_route_passes_message_id(client) -> None:
    with patch(
        "app.api.routes.github.github_insight_service.suggest_repo_insights",
        new=AsyncMock(return_value=[]),
    ) as mock_suggest:
        await client.get(
            "/api/v1/github/insights/suggest",
            params={
                "repo": "The-Velveteen-Project/StochastoGreen",
                "message_id": 33,
            },
        )

    assert mock_suggest.call_args.kwargs["message_id"] == 33
