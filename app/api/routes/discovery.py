"""
Discovery route — signal suggestion endpoint.

GET /api/v1/discovery/suggest?q=<query>[&limit=<n>]

Returns up to `limit` ranked signals from arXiv and Hacker News.
Results are also persisted to the `signals` table before being returned.
"""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, Query

from app.core.config import settings
from app.db.session import get_db
from app.schemas.discovery import SignalResponse, SuggestResponse
from app.services import discovery_service

router = APIRouter(tags=["discovery"])


@router.get("/suggest", response_model=SuggestResponse)
async def suggest(
    q: Annotated[
        str,
        Query(
            min_length=2,
            max_length=200,
            description="Free-text search query (e.g. 'agentic workflows latam')",
        ),
    ],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=10,
            description="Number of signals to return (default from config).",
        ),
    ] = settings.discovery_default_limit,
    message_id: Annotated[
        int | None,
        Query(
            ge=1,
            description="Optional internal messages.id that originated the query.",
        ),
    ] = None,
    db: Annotated[aiosqlite.Connection, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> SuggestResponse:
    """
    Discover, rank, and persist signal candidates for the provided query.

    This endpoint is intentionally stateful in Phase 4: returned candidates
    are written to `signals` before the response is sent back to the client.
    It is not a read-only preview endpoint.
    """
    candidates = await discovery_service.discover(
        q,
        db,
        limit=limit,
        message_id=message_id,
    )
    return SuggestResponse(
        query=q,
        total_candidates=len(candidates),
        signals=[SignalResponse.from_candidate(c) for c in candidates],
    )
