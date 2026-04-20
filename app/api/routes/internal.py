"""
Internal authenticated routes for production cron execution.

These endpoints are intended for Railway Cron Jobs or other trusted internal
callers. They trigger the same job runners used by the local scheduler.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings
from app.services.scheduler import run_weekly_mvp_scan_job, run_weekly_summary_job

router = APIRouter(tags=["internal"])


class InternalJobResponse(BaseModel):
    status: str
    job: str


def _require_internal_token(x_internal_token: str | None) -> None:
    configured = settings.internal_cron_secret.strip()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_CRON_SECRET is not configured.",
        )
    if x_internal_token != configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal token.",
        )


@router.post("/run-weekly-summary", response_model=InternalJobResponse)
async def run_weekly_summary(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> InternalJobResponse:
    _require_internal_token(x_internal_token)
    await run_weekly_summary_job()
    return InternalJobResponse(status="ok", job="weekly_summary")


@router.post("/run-mvp-scan", response_model=InternalJobResponse)
async def run_mvp_scan(
    x_internal_token: Annotated[str | None, Header()] = None,
) -> InternalJobResponse:
    _require_internal_token(x_internal_token)
    await run_weekly_mvp_scan_job()
    return InternalJobResponse(status="ok", job="weekly_mvp_scan")
