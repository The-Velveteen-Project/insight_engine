import os

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    commit: str


def _commit_sha() -> str:
    """Railway injects RAILWAY_GIT_COMMIT_SHA; fall back to GIT_COMMIT_SHA."""
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get(
        "GIT_COMMIT_SHA", ""
    )
    return sha[:12] if sha else "unknown"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0", commit=_commit_sha())
