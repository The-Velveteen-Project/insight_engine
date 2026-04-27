"""
Discovery schemas — external-facing data contracts for the signal pipeline.

SignalCandidate  : full internal representation.
SignalResponse   : API-safe projection of SignalCandidate.
DiscoverySuggest : query parameters for GET /api/v1/discovery/suggest.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, HttpUrl

SourceType = Literal["arxiv", "hackernews", "github", "exa"]


class SignalCandidate(BaseModel):
    """
    A single ranked signal from any discovery source.

    source_type  : provider key
    source_id    : provider-native identifier (arXiv ID, HN item ID)
    title        : article / paper title
    url          : canonical link to the resource
    summary      : abstract / excerpt (≤ 500 chars preferred)
    raw_content  : full provider payload — persisted, never returned via API
    relevance_score : float in [0.0, 1.0] — higher = more relevant to profile
    relevance_note  : human-readable explanation of the score
    published_at    : provider's publication timestamp (UTC), or None
    """

    source_type: SourceType
    source_id: str
    title: str
    url: HttpUrl
    summary: str
    raw_content: str = ""
    relevance_score: float = 0.0
    relevance_note: str = ""
    published_at: datetime | None = None


class SignalResponse(BaseModel):
    """
    API-safe projection of SignalCandidate — omits raw_content.
    """

    source_type: SourceType
    source_id: str
    title: str
    url: str
    summary: str
    relevance_score: float
    relevance_note: str
    published_at: datetime | None = None

    @classmethod
    def from_candidate(cls, c: SignalCandidate) -> Self:
        return cls(
            source_type=c.source_type,
            source_id=c.source_id,
            title=c.title,
            url=str(c.url),
            summary=c.summary,
            relevance_score=c.relevance_score,
            relevance_note=c.relevance_note,
            published_at=c.published_at,
        )


class SuggestResponse(BaseModel):
    query: str
    total_candidates: int
    signals: list[SignalResponse]
