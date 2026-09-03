import os

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings
from app.services.diagnostics import llm_base_host

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    commit: str
    llm_model: str
    llm_base_host: str
    sources: list[str]


def _commit_sha() -> str:
    """Railway injects RAILWAY_GIT_COMMIT_SHA; fall back to GIT_COMMIT_SHA."""
    sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get(
        "GIT_COMMIT_SHA", ""
    )
    return sha[:12] if sha else "unknown"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        commit=_commit_sha(),
        llm_model=settings.editorial_model,
        llm_base_host=llm_base_host(),
        sources=list(settings.enabled_discovery_sources),
    )
