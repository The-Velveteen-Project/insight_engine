"""
Active-goal service for Sub-phase B.

A single goal is active at any time. Setting a new goal archives the previous
one. The goal text and deadline are read by the editorial planner and the
weekly thesis generator so that filtering and synthesis stay anchored to
what Carlos is actually trying to land in this horizon.

The env var ACTIVE_GOAL_TEXT is used only as a one-shot seed: the first time
the service is consulted on an empty database, a goal is created from it.
"""

from __future__ import annotations

import logging
from datetime import datetime

import aiosqlite

from app.core.config import settings
from app.db.queries import (
    archive_active_goals,
    get_active_goal_by_id,
    get_current_active_goal,
    insert_active_goal,
)
from app.schemas.goals import ActiveGoal

logger = logging.getLogger(__name__)


def _row_to_active_goal(row: aiosqlite.Row) -> ActiveGoal:
    return ActiveGoal(
        id=int(row["id"]),
        label=str(row["label"]),
        description=(
            str(row["description"]) if row["description"] is not None else None
        ),
        deadline_at=row["deadline_at"],
        created_at=row["created_at"],
        archived_at=row["archived_at"],
    )


async def get_current(db: aiosqlite.Connection) -> ActiveGoal | None:
    row = await get_current_active_goal(db)
    if row is None:
        return None
    return _row_to_active_goal(row)


async def get_by_id(db: aiosqlite.Connection, goal_id: int) -> ActiveGoal | None:
    row = await get_active_goal_by_id(db, goal_id)
    if row is None:
        return None
    return _row_to_active_goal(row)


async def set_active(
    db: aiosqlite.Connection,
    *,
    label: str,
    description: str | None = None,
    deadline_at: datetime | None = None,
) -> ActiveGoal:
    """Archives any current active goal and inserts the new one."""
    await archive_active_goals(db)
    deadline_iso = deadline_at.isoformat() if deadline_at is not None else None
    goal_id = await insert_active_goal(
        db,
        label=label.strip(),
        description=description.strip() if description else None,
        deadline_at=deadline_iso,
    )
    row = await get_active_goal_by_id(db, goal_id)
    assert row is not None
    return _row_to_active_goal(row)


async def clear_active(db: aiosqlite.Connection) -> ActiveGoal | None:
    """Archives the current active goal. Returns it (post-archive) or None."""
    current = await get_current_active_goal(db)
    if current is None:
        return None
    goal_id = int(current["id"])
    await archive_active_goals(db)
    row = await get_active_goal_by_id(db, goal_id)
    assert row is not None
    return _row_to_active_goal(row)


async def seed_from_env_if_empty(db: aiosqlite.Connection) -> ActiveGoal | None:
    """Creates a goal from ACTIVE_GOAL_TEXT iff none exists yet.

    Idempotent — runs only when there is no active goal in the database.
    Used at app startup so a redeploy with the env var set bootstraps the
    goal once, then the user manages it via /goal from then on.
    """
    existing = await get_current_active_goal(db)
    if existing is not None:
        return _row_to_active_goal(existing)
    seed = settings.active_goal_text.strip()
    if not seed:
        return None
    logger.info("Seeding active goal from ACTIVE_GOAL_TEXT env var.")
    return await set_active(db, label=seed)


def days_remaining(goal: ActiveGoal, now: datetime | None = None) -> int | None:
    """Whole days from `now` to `goal.deadline_at`. Negative if past due."""
    if goal.deadline_at is None:
        return None
    reference = now or datetime.now(tz=goal.deadline_at.tzinfo)
    delta = goal.deadline_at - reference
    return delta.days
