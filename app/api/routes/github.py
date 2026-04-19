"""
GitHub insight route — inspect one or more public repositories.

GET /api/v1/github/insights/suggest?repo=owner/name&repo=owner/name2[&limit=<n>]
"""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, Query

from app.core.config import settings
from app.db.session import get_db
from app.schemas.github import GitHubInsightResponse, GitHubSuggestResponse
from app.services import github_insight_service

router = APIRouter(tags=["github"])


@router.get("/insights/suggest", response_model=GitHubSuggestResponse)
async def suggest_github_insights(
    repo: Annotated[
        list[str],
        Query(
            description="One or more public repos in owner/name form.",
        ),
    ],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=20,
            description="Maximum number of insight candidates to return.",
        ),
    ] = settings.github_insights_default_limit,
    message_id: Annotated[
        int | None,
        Query(
            ge=1,
            description="Optional internal messages.id that originated the query.",
        ),
    ] = None,
    db: Annotated[aiosqlite.Connection, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> GitHubSuggestResponse:
    """
    Inspect, rank, and persist GitHub insight candidates for the requested repos.

    This endpoint is intentionally stateful in Phase 5: returned candidates
    are written to `signals` before the response is sent back to the client.
    It is not a read-only preview endpoint.
    """
    candidates = await github_insight_service.suggest_repo_insights(
        repo,
        db,
        limit=limit,
        message_id=message_id,
    )
    return GitHubSuggestResponse(
        repos=repo,
        total_candidates=len(candidates),
        insights=[GitHubInsightResponse.from_candidate(item) for item in candidates],
    )
