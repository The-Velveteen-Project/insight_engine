"""
Post ledger (Phase 1 of the career manager).

Every LinkedIn post the operator generates is recorded. Carlos marks the
ones he actually publishes ("publicado <url>"), including posts he wrote
by hand outside the operator. Cadence is computed only from published
rows, so the number the operator reports is "posts that went live", never
"drafts that exist".

The cadence reminder is deliberately quiet: it fires only when the weekly
target is not met and at most once per configured gap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from app.core.config import settings
from app.db.queries import (
    count_linkedin_posts_published_since,
    get_last_published_at,
    get_latest_generated_linkedin_post,
    get_linkedin_post_by_id,
    get_operator_state,
    get_recent_linkedin_posts,
    insert_linkedin_post,
    mark_linkedin_post_published,
    set_operator_state,
)
from app.schemas.linkedin import LinkedInPost, LinkedInPostRecord, PostStatus
from app.services import active_goals

logger = logging.getLogger(__name__)

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_REMINDER_KEY = "cadence_last_reminded_at"
_MANUAL_BODY = "Post publicado por fuera del operador."


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def stamp(moment: datetime) -> str:
    """SQLite-compatible UTC timestamp, same shape as CURRENT_TIMESTAMP."""
    return moment.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)


def parse_stamp(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, _TIMESTAMP_FORMAT)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row_to_record(row: aiosqlite.Row) -> LinkedInPostRecord:
    created = parse_stamp(row["created_at"]) or datetime.now(UTC)
    return LinkedInPostRecord(
        id=int(row["id"]),
        plan_id=int(row["plan_id"]) if row["plan_id"] is not None else None,
        goal_id=int(row["goal_id"]) if row["goal_id"] is not None else None,
        chat_id=int(row["chat_id"]) if row["chat_id"] is not None else None,
        body=str(row["body"]),
        hook=str(row["hook"]) if row["hook"] is not None else None,
        language=str(row["language"] or "es"),
        llm_used=bool(row["llm_used"]),
        model=str(row["model"]) if row["model"] is not None else None,
        opinion_used=bool(row["opinion_used"]),
        status=PostStatus(str(row["status"])),
        published_url=(
            str(row["published_url"]) if row["published_url"] is not None else None
        ),
        published_at=parse_stamp(row["published_at"]),
        created_at=created,
    )


@dataclass(frozen=True)
class PublishResult:
    record: LinkedInPostRecord
    created_manual: bool


@dataclass(frozen=True)
class CadenceStatus:
    published_last_7d: int
    target_per_week: int
    last_published_at: datetime | None
    unpublished: list[LinkedInPostRecord]

    @property
    def on_track(self) -> bool:
        return self.published_last_7d >= self.target_per_week

    def days_since_last(self, now: datetime | None = None) -> int | None:
        if self.last_published_at is None:
            return None
        return max((_now(now) - self.last_published_at).days, 0)


async def record_generated(
    db: aiosqlite.Connection,
    *,
    plan_id: int | None,
    chat_id: int | None,
    post: LinkedInPost,
    llm_used: bool,
    opinion_used: bool,
    language: str = "es",
) -> int:
    goal = await active_goals.get_current(db)
    post_id = await insert_linkedin_post(
        db,
        plan_id=plan_id,
        goal_id=goal.id if goal is not None else None,
        chat_id=chat_id,
        body=post.assembled_body(),
        hook=post.hook.strip(),
        language=language,
        llm_used=llm_used,
        model=settings.editorial_model if llm_used else None,
        opinion_used=opinion_used,
    )
    logger.info("Recorded generated LinkedIn post id=%d plan=%s.", post_id, plan_id)
    return post_id


async def get_record(db: aiosqlite.Connection, post_id: int) -> LinkedInPostRecord:
    row = await get_linkedin_post_by_id(db, post_id)
    if row is None:
        raise LookupError(f"LinkedIn post {post_id} not found.")
    return _row_to_record(row)


async def mark_published(
    db: aiosqlite.Connection,
    *,
    chat_id: int | None,
    post_id: int | None,
    url: str | None,
    now: datetime | None = None,
) -> PublishResult:
    """Mark a generated post as live, or record a manual post when none exists.

    Resolution order: explicit id, then the latest generated post for this
    chat, then a new manual row. A manual row is how posts written outside
    the operator still count toward cadence.
    """
    published_at = stamp(_now(now))
    row = None
    if post_id is not None:
        row = await get_linkedin_post_by_id(db, post_id)
        if row is None:
            raise LookupError(f"LinkedIn post {post_id} not found.")
    else:
        row = await get_latest_generated_linkedin_post(db, chat_id=chat_id)

    if row is not None:
        resolved_id = int(row["id"])
        await mark_linkedin_post_published(
            db, post_id=resolved_id, published_url=url, published_at=published_at
        )
        return PublishResult(
            record=await get_record(db, resolved_id), created_manual=False
        )

    goal = await active_goals.get_current(db)
    manual_id = await insert_linkedin_post(
        db,
        plan_id=None,
        goal_id=goal.id if goal is not None else None,
        chat_id=chat_id,
        body=url or _MANUAL_BODY,
        hook=None,
        language="es",
        llm_used=False,
        model=None,
        opinion_used=False,
        status=PostStatus.PUBLISHED.value,
        published_url=url,
        published_at=published_at,
    )
    return PublishResult(record=await get_record(db, manual_id), created_manual=True)


async def list_recent(
    db: aiosqlite.Connection, *, limit: int = 8
) -> list[LinkedInPostRecord]:
    rows = await get_recent_linkedin_posts(db, limit=limit)
    return [_row_to_record(row) for row in rows]


async def cadence_status(
    db: aiosqlite.Connection, now: datetime | None = None
) -> CadenceStatus:
    moment = _now(now)
    since = stamp(moment - timedelta(days=7))
    published = await count_linkedin_posts_published_since(db, since)
    last_raw = await get_last_published_at(db)
    unpublished_rows = await get_recent_linkedin_posts(
        db, limit=5, status=PostStatus.GENERATED.value
    )
    return CadenceStatus(
        published_last_7d=published,
        target_per_week=max(settings.post_cadence_per_week, 1),
        last_published_at=parse_stamp(last_raw),
        unpublished=[_row_to_record(row) for row in unpublished_rows],
    )


async def build_cadence_reminder(
    db: aiosqlite.Connection, now: datetime | None = None
) -> str | None:
    """Return the reminder text when one is due, else None.

    Due means: weekly target not met AND no reminder within the configured
    gap. Sending is the caller's job; this only decides and records.
    """
    moment = _now(now)
    status = await cadence_status(db, moment)
    if status.on_track:
        return None
    last_reminded = parse_stamp(await get_operator_state(db, _REMINDER_KEY))
    gap = timedelta(hours=max(settings.cadence_reminder_min_gap_hours, 1))
    if last_reminded is not None and moment - last_reminded < gap:
        return None

    from app.utils import telegram_formatting  # local import: avoids a cycle

    await set_operator_state(db, _REMINDER_KEY, stamp(moment))
    return telegram_formatting.format_cadence_reminder(status, now=moment)
