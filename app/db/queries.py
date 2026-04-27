from __future__ import annotations

import json
from typing import cast

import aiosqlite

from app.domain.message import Message
from app.schemas.discovery import SignalCandidate
from app.schemas.drafts import EditorialDraft, EditorialDraftStatus
from app.schemas.editorial import EditorialPlan, EditorialPlanStatus


async def insert_message(db: aiosqlite.Connection, message: Message) -> int:
    cursor = await db.execute(
        """
        INSERT INTO messages (
            telegram_message_id, telegram_chat_id, user_id, username,
            text, source_url, voice_file_id, voice_duration,
            reply_to_telegram_id,
            has_url, is_reply,
            message_type, channel, status,
            transcription, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message.telegram_message_id,
            message.telegram_chat_id,
            message.user_id,
            message.username,
            message.text,
            message.source_url,
            message.voice_file_id,
            message.voice_duration,
            message.reply_to_telegram_id,
            int(message.has_url),
            int(message.is_reply),
            message.message_type,
            message.channel,
            message.status,
            message.transcription,
            message.raw_payload,
        ),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def update_message_status(
    db: aiosqlite.Connection,
    message_id: int,
    status: str,
) -> None:
    """updated_at is handled automatically by the DB trigger on any UPDATE."""
    await db.execute(
        "UPDATE messages SET status = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, message_id),
    )
    await db.commit()


async def get_message_by_id(
    db: aiosqlite.Connection, message_id: int
) -> aiosqlite.Row | None:
    cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
    return await cursor.fetchone()


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


async def insert_signal(
    db: aiosqlite.Connection,
    signal: SignalCandidate,
    *,
    message_id: int | None = None,
) -> int:
    """
    Persist or refresh a SignalCandidate in the signals table.

    `message_id` links the signal to the Telegram message that triggered
    the discovery query (optional — discovery can also run on a schedule).
    """
    identity_cursor = await db.execute(
        """
        SELECT id, message_id
        FROM signals
        WHERE source_type = ? AND source_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (signal.source_type, signal.source_id),
    )
    existing = await identity_cursor.fetchone()
    published_at = signal.published_at.isoformat() if signal.published_at else None

    if existing is None:
        cursor = await db.execute(
            """
            INSERT INTO signals (
                source_type, source_id, title, url, summary,
                raw_content, relevance_score, relevance_note,
                message_id, published_at, evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                signal.source_type,
                signal.source_id,
                signal.title,
                str(signal.url),
                signal.summary,
                signal.raw_content,
                signal.relevance_score,
                signal.relevance_note,
                message_id,
                published_at,
            ),
        )
        await db.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    persisted_message_id = cast(int | None, existing["message_id"])
    signal_id = cast(int, existing["id"])
    await db.execute(
        """
        UPDATE signals
        SET
            title = ?,
            url = ?,
            summary = ?,
            raw_content = ?,
            relevance_score = ?,
            relevance_note = ?,
            message_id = ?,
            published_at = ?,
            evaluated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            signal.title,
            str(signal.url),
            signal.summary,
            signal.raw_content,
            signal.relevance_score,
            signal.relevance_note,
            persisted_message_id if persisted_message_id is not None else message_id,
            published_at,
            signal_id,
        ),
    )
    await db.commit()
    return signal_id


async def get_recent_signals(
    db: aiosqlite.Connection,
    *,
    limit: int = 20,
    source_type: str | None = None,
) -> list[aiosqlite.Row]:
    """
    Returns up to `limit` signals ordered by created_at DESC.
    Optionally filtered by source_type ('arxiv' | 'hackernews' | 'github').
    """
    if source_type:
        cursor = await db.execute(
            """
            SELECT * FROM signals
            WHERE source_type = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (source_type, limit),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    rows = await cursor.fetchall()
    return list(rows)


async def get_signals_by_ids(
    db: aiosqlite.Connection,
    signal_ids: list[int],
) -> list[aiosqlite.Row]:
    """
    Fetches signals by id while preserving the caller's requested order.
    Missing ids are ignored.
    """
    if not signal_ids:
        return []

    placeholders = ", ".join("?" for _ in signal_ids)
    cursor = await db.execute(
        f"SELECT * FROM signals WHERE id IN ({placeholders})",
        tuple(signal_ids),
    )
    rows = await cursor.fetchall()
    rows_by_id = {cast(int, row["id"]): row for row in rows}
    return [
        rows_by_id[signal_id] for signal_id in signal_ids if signal_id in rows_by_id
    ]


async def get_signal_by_source_identity(
    db: aiosqlite.Connection,
    *,
    source_type: str,
    source_id: str,
) -> aiosqlite.Row | None:
    cursor = await db.execute(
        """
        SELECT *
        FROM signals
        WHERE source_type = ? AND source_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (source_type, source_id),
    )
    return await cursor.fetchone()


# ---------------------------------------------------------------------------
# Editorial plans
# ---------------------------------------------------------------------------


async def insert_editorial_plan(
    db: aiosqlite.Connection,
    proposal: EditorialPlan,
    *,
    status: EditorialPlanStatus = EditorialPlanStatus.DRAFT,
    goal_id: int | None = None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO editorial_plans (
            signal_ids,
            recommended_action,
            confidence,
            proposal_json,
            status,
            llm_used,
            fallback_used,
            goal_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            json.dumps(proposal.signal_ids),
            proposal.recommended_action.value,
            proposal.confidence,
            proposal.model_dump_json(),
            status.value,
            int(proposal.llm_used),
            int(proposal.fallback_used),
            goal_id,
        ),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def get_editorial_plan_by_id(
    db: aiosqlite.Connection,
    plan_id: int,
) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT * FROM editorial_plans WHERE id = ?",
        (plan_id,),
    )
    return await cursor.fetchone()


async def get_recent_editorial_plans(
    db: aiosqlite.Connection,
    *,
    limit: int = 10,
) -> list[aiosqlite.Row]:
    cursor = await db.execute(
        """
        SELECT *
        FROM editorial_plans
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = await cursor.fetchall()
    return list(rows)


async def update_editorial_plan_status(
    db: aiosqlite.Connection,
    plan_id: int,
    status: EditorialPlanStatus,
) -> None:
    await db.execute(
        """
        UPDATE editorial_plans
        SET
            status = ?,
            updated_at = CURRENT_TIMESTAMP,
            reviewed_at = COALESCE(reviewed_at, CURRENT_TIMESTAMP)
        WHERE id = ?
        """,
        (status.value, plan_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Editorial drafts
# ---------------------------------------------------------------------------


async def insert_editorial_draft(
    db: aiosqlite.Connection,
    draft: EditorialDraft,
    *,
    status: EditorialDraftStatus = EditorialDraftStatus.DRAFT,
    goal_id: int | None = None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO editorial_drafts (
            plan_id,
            draft_json,
            status,
            llm_used,
            fallback_used,
            goal_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            draft.plan_id,
            draft.model_dump_json(),
            status.value,
            int(draft.llm_used),
            int(draft.fallback_used),
            goal_id,
        ),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def get_editorial_draft_by_id(
    db: aiosqlite.Connection,
    draft_id: int,
) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT * FROM editorial_drafts WHERE id = ?",
        (draft_id,),
    )
    return await cursor.fetchone()


async def get_editorial_draft_by_plan_id(
    db: aiosqlite.Connection,
    plan_id: int,
) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT * FROM editorial_drafts WHERE plan_id = ?",
        (plan_id,),
    )
    return await cursor.fetchone()


async def get_recent_editorial_drafts(
    db: aiosqlite.Connection,
    *,
    limit: int = 10,
) -> list[aiosqlite.Row]:
    cursor = await db.execute(
        """
        SELECT *
        FROM editorial_drafts
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = await cursor.fetchall()
    return list(rows)


async def update_editorial_draft_status(
    db: aiosqlite.Connection,
    draft_id: int,
    status: EditorialDraftStatus,
) -> None:
    await db.execute(
        """
        UPDATE editorial_drafts
        SET
            status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status.value, draft_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Telegram sessions
# ---------------------------------------------------------------------------


async def get_telegram_session(
    db: aiosqlite.Connection,
    chat_id: int,
) -> aiosqlite.Row | None:
    cursor = await db.execute(
        """
        SELECT *
        FROM telegram_sessions
        WHERE chat_id = ?
        """,
        (chat_id,),
    )
    return await cursor.fetchone()


# ---------------------------------------------------------------------------
# Active goal (Sub-phase B)
# ---------------------------------------------------------------------------


async def get_current_active_goal(
    db: aiosqlite.Connection,
) -> aiosqlite.Row | None:
    """Returns the most recent non-archived goal, or None."""
    cursor = await db.execute(
        """
        SELECT * FROM active_goals
        WHERE archived_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
    )
    return await cursor.fetchone()


async def insert_active_goal(
    db: aiosqlite.Connection,
    *,
    label: str,
    description: str | None,
    deadline_at: str | None,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO active_goals (label, description, deadline_at)
        VALUES (?, ?, ?)
        """,
        (label, description, deadline_at),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def archive_active_goals(db: aiosqlite.Connection) -> int:
    """Archives every currently-active goal. Returns the count archived."""
    cursor = await db.execute(
        """
        UPDATE active_goals
        SET archived_at = CURRENT_TIMESTAMP
        WHERE archived_at IS NULL
        """,
    )
    await db.commit()
    return cursor.rowcount or 0


async def get_active_goal_by_id(
    db: aiosqlite.Connection,
    goal_id: int,
) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT * FROM active_goals WHERE id = ?",
        (goal_id,),
    )
    return await cursor.fetchone()


# ---------------------------------------------------------------------------
# Handoff follow-ups (Sub-phase B — "después" path)
# ---------------------------------------------------------------------------


async def insert_handoff_followup(
    db: aiosqlite.Connection,
    *,
    plan_id: int,
    chat_id: int,
    due_at: str,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO pending_handoff_followups (plan_id, chat_id, due_at)
        VALUES (?, ?, ?)
        """,
        (plan_id, chat_id, due_at),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def get_due_handoff_followups(
    db: aiosqlite.Connection,
) -> list[aiosqlite.Row]:
    """Pending followups whose due_at has passed. Caller iterates and notifies.

    Uses ``datetime()`` on both sides so the comparison normalizes string
    formats — ISO 8601 with offset (``2026-04-27T01:00:00+00:00``) and
    SQLite's ``CURRENT_TIMESTAMP`` shape (``2026-04-27 01:00:00``) compare
    correctly. A pure text ``<=`` would silently never match because the
    ``T`` separator sorts after the space.
    """
    cursor = await db.execute(
        """
        SELECT * FROM pending_handoff_followups
        WHERE status = 'pending'
          AND datetime(due_at) <= datetime('now')
        ORDER BY due_at ASC
        """,
    )
    rows = await cursor.fetchall()
    return list(rows)


async def get_pending_handoff_followups_for_chat(
    db: aiosqlite.Connection,
    chat_id: int,
) -> list[aiosqlite.Row]:
    cursor = await db.execute(
        """
        SELECT * FROM pending_handoff_followups
        WHERE chat_id = ? AND status IN ('pending', 'notified')
        ORDER BY id DESC
        """,
        (chat_id,),
    )
    rows = await cursor.fetchall()
    return list(rows)


async def mark_handoff_followup_notified(
    db: aiosqlite.Connection,
    followup_id: int,
) -> None:
    await db.execute(
        """
        UPDATE pending_handoff_followups
        SET status = 'notified', notified_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (followup_id,),
    )
    await db.commit()


async def mark_handoff_followup_dismissed(
    db: aiosqlite.Connection,
    followup_id: int,
) -> None:
    await db.execute(
        """
        UPDATE pending_handoff_followups
        SET status = 'dismissed'
        WHERE id = ?
        """,
        (followup_id,),
    )
    await db.commit()


async def upsert_telegram_session(
    db: aiosqlite.Connection,
    *,
    chat_id: int,
    last_signal_ids: list[int],
    last_plan_id: int | None,
    last_draft_id: int | None,
    pending_command: str | None,
    pending_target_id: int | None,
) -> None:
    await db.execute(
        """
        INSERT INTO telegram_sessions (
            chat_id,
            last_signal_ids,
            last_plan_id,
            last_draft_id,
            pending_command,
            pending_target_id,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET
            last_signal_ids = excluded.last_signal_ids,
            last_plan_id = excluded.last_plan_id,
            last_draft_id = excluded.last_draft_id,
            pending_command = excluded.pending_command,
            pending_target_id = excluded.pending_target_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            chat_id,
            json.dumps(last_signal_ids),
            last_plan_id,
            last_draft_id,
            pending_command,
            pending_target_id,
        ),
    )
    await db.commit()
