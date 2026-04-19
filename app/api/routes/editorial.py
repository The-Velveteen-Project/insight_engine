"""
Editorial planning and review routes for Phases 6-7.

POST /api/v1/editorial/plan
"""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, status

from app.db.session import get_db
from app.schemas.editorial import (
    EditorialPlanRequest,
    EditorialPlanStatus,
    PersistedEditorialPlan,
)
from app.services import editorial_planner

router = APIRouter(tags=["editorial"])


@router.post("/plan", response_model=PersistedEditorialPlan)
async def create_editorial_plan(
    payload: EditorialPlanRequest,
    db: Annotated[aiosqlite.Connection, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> PersistedEditorialPlan:
    """
    Build a structured editorial proposal from already persisted signals.

    This endpoint is stateful in Phase 7: it generates the proposal, persists
    it as an editorial plan in `draft` status, and then returns the stored
    record for human review. It does not publish or approve anything.
    """
    try:
        return await editorial_planner.create_persisted_editorial_plan(
            db, payload.signal_ids
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/plans/{plan_id}", response_model=PersistedEditorialPlan)
async def get_editorial_plan(
    plan_id: int,
    db: Annotated[aiosqlite.Connection, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> PersistedEditorialPlan:
    try:
        return await editorial_planner.get_persisted_editorial_plan(db, plan_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


async def _transition_plan(
    db: aiosqlite.Connection,
    *,
    plan_id: int,
    target_status: EditorialPlanStatus,
) -> PersistedEditorialPlan:
    try:
        return await editorial_planner.transition_editorial_plan(
            db,
            plan_id,
            target_status,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except editorial_planner.EditorialPlanTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.post("/plans/{plan_id}/approve", response_model=PersistedEditorialPlan)
async def approve_editorial_plan(
    plan_id: int,
    db: Annotated[aiosqlite.Connection, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> PersistedEditorialPlan:
    """
    Valid transitions:
    - draft -> approved
    - approved -> saved
    - saved and discarded are terminal
    """
    return await _transition_plan(
        db,
        plan_id=plan_id,
        target_status=EditorialPlanStatus.APPROVED,
    )


@router.post("/plans/{plan_id}/save", response_model=PersistedEditorialPlan)
async def save_editorial_plan(
    plan_id: int,
    db: Annotated[aiosqlite.Connection, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> PersistedEditorialPlan:
    return await _transition_plan(
        db,
        plan_id=plan_id,
        target_status=EditorialPlanStatus.SAVED,
    )


@router.post("/plans/{plan_id}/discard", response_model=PersistedEditorialPlan)
async def discard_editorial_plan(
    plan_id: int,
    db: Annotated[aiosqlite.Connection, Depends(get_db)] = ...,  # type: ignore[assignment]
) -> PersistedEditorialPlan:
    return await _transition_plan(
        db,
        plan_id=plan_id,
        target_status=EditorialPlanStatus.DISCARDED,
    )
