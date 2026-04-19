"""
GitHub-specific schemas for Phase 5.

These models describe GitHub metadata and the intermediate insight candidates
before they are converted into the shared `signals` persistence shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel

GitHubInsightType = Literal["overview", "activity", "artifact"]


class GitHubRepoMetadata(BaseModel):
    full_name: str
    name: str
    owner_login: str
    description: str | None = None
    html_url: str
    topics: list[str] = []
    language: str | None = None
    stargazers_count: int = 0
    default_branch: str = "main"
    archived: bool = False
    updated_at: datetime | None = None


class GitHubCommitSummary(BaseModel):
    sha: str
    message: str
    html_url: str
    committed_at: datetime | None = None


class GitHubContentEntry(BaseModel):
    name: str
    path: str
    type: Literal["file", "dir"]
    size: int = 0
    html_url: str | None = None


class GitHubTextFile(BaseModel):
    path: str
    html_url: str | None = None
    text: str
    truncated: bool = False


class GitHubInsightCandidate(BaseModel):
    repo_full_name: str
    insight_type: GitHubInsightType
    source_id: str
    title: str
    url: str
    summary: str
    evidence: list[str] = []
    raw_content: str = ""
    relevance_score: float = 0.0
    relevance_note: str = ""
    published_at: datetime | None = None


class GitHubInsightResponse(BaseModel):
    repo_full_name: str
    insight_type: GitHubInsightType
    source_id: str
    title: str
    url: str
    summary: str
    evidence: list[str]
    relevance_score: float
    relevance_note: str
    published_at: datetime | None = None

    @classmethod
    def from_candidate(cls, candidate: GitHubInsightCandidate) -> Self:
        return cls(
            repo_full_name=candidate.repo_full_name,
            insight_type=candidate.insight_type,
            source_id=candidate.source_id,
            title=candidate.title,
            url=str(candidate.url),
            summary=candidate.summary,
            evidence=candidate.evidence,
            relevance_score=candidate.relevance_score,
            relevance_note=candidate.relevance_note,
            published_at=candidate.published_at,
        )


class GitHubSuggestResponse(BaseModel):
    repos: list[str]
    total_candidates: int
    insights: list[GitHubInsightResponse]
