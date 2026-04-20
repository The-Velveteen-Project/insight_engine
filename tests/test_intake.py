"""
Integration tests for the intake pipeline.
Tests DB persistence, UNIQUE constraint, updated_at trigger, and the full
handle_update flow with Telegram client mocked.
"""

import json
import sqlite3
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.db.queries import get_message_by_id, insert_message, update_message_status
from app.domain.message import Message
from app.schemas.telegram import TelegramUpdate
from app.services.message_intake import handle_update

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_message(
    *,
    telegram_message_id: int,
    telegram_chat_id: int = 100,
    text: str = "test",
    message_type: str = "text",
) -> Message:
    return Message(
        telegram_message_id=telegram_message_id,
        telegram_chat_id=telegram_chat_id,
        user_id=42,
        username="tester",
        text=text,
        message_type=message_type,
        raw_payload='{"update_id": 1}',
    )


def _make_update(
    *,
    message_id: int,
    chat_id: int = 100,
    text: str = "hello",
    reply_to_id: int | None = None,
) -> tuple[TelegramUpdate, str]:
    payload: dict = {
        "update_id": 300000 + message_id,
        "message": {
            "message_id": message_id,
            "from": {"id": 42, "is_bot": False, "first_name": "User"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1700000000,
            "text": text,
        },
    }
    if reply_to_id is not None:
        payload["message"]["reply_to_message"] = {
            "message_id": reply_to_id,
            "from": {"id": 99, "is_bot": False, "first_name": "Other"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1699990000,
            "text": "original",
        }
    raw = json.dumps(payload)
    return TelegramUpdate.model_validate(payload), raw


def _make_voice_update(
    *,
    message_id: int,
    chat_id: int = 100,
    file_id: str = "voice_file_id_123",
    duration: int = 10,
) -> tuple[TelegramUpdate, str]:
    payload = {
        "update_id": 400000 + message_id,
        "message": {
            "message_id": message_id,
            "from": {"id": 42, "is_bot": False, "first_name": "User"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1700000000,
            "voice": {
                "file_id": file_id,
                "file_unique_id": f"u{message_id}",
                "duration": duration,
            },
        },
    }
    raw = json.dumps(payload)
    return TelegramUpdate.model_validate(payload), raw


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


async def test_insert_message_persists(db: aiosqlite.Connection) -> None:
    msg = _make_message(telegram_message_id=1001)
    msg_id = await insert_message(db, msg)
    row = await get_message_by_id(db, msg_id)

    assert row is not None
    assert row["telegram_message_id"] == 1001
    assert row["telegram_chat_id"] == 100
    assert row["text"] == "test"
    assert row["status"] == "received"


async def test_insert_message_persists_flags(db: aiosqlite.Connection) -> None:
    msg = _make_message(telegram_message_id=1002)
    msg.has_url = True
    msg.is_reply = True
    msg.source_url = "https://example.com"
    msg.reply_to_telegram_id = 5

    msg_id = await insert_message(db, msg)
    row = await get_message_by_id(db, msg_id)

    assert row is not None
    assert bool(row["has_url"]) is True
    assert bool(row["is_reply"]) is True
    assert row["source_url"] == "https://example.com"
    assert row["reply_to_telegram_id"] == 5


# ---------------------------------------------------------------------------
# UNIQUE constraint — explicit requirement from the user
# ---------------------------------------------------------------------------


async def test_duplicate_message_raises_integrity_error(
    db: aiosqlite.Connection,
) -> None:
    """Two messages with the same (telegram_chat_id, telegram_message_id) must fail."""
    msg = _make_message(telegram_message_id=9001, telegram_chat_id=9001)
    await insert_message(db, msg)

    with pytest.raises(sqlite3.IntegrityError):
        await insert_message(db, msg)


# ---------------------------------------------------------------------------
# updated_at trigger — explicit requirement from the user
# ---------------------------------------------------------------------------


async def test_updated_at_changes_on_status_update(
    db: aiosqlite.Connection,
) -> None:
    """
    Pin updated_at to a known past value, then call update_message_status.
    The DB trigger (trg_messages_updated_at) must set updated_at = CURRENT_TIMESTAMP.
    """
    msg = _make_message(telegram_message_id=8001, telegram_chat_id=8001)
    msg_id = await insert_message(db, msg)

    # Anchor updated_at to a known past value.
    # The trigger's WHEN clause (NEW.updated_at = OLD.updated_at) evaluates to
    # False here (we're explicitly changing updated_at), so the trigger does NOT
    # fire — the past value sticks as intended.
    past = "2020-01-01 00:00:00"
    await db.execute("UPDATE messages SET updated_at = ? WHERE id = ?", (past, msg_id))
    await db.commit()

    row_before = await get_message_by_id(db, msg_id)
    assert row_before is not None
    assert row_before["updated_at"] == past

    # Now trigger a status update. This UPDATE does not touch updated_at,
    # so NEW.updated_at = OLD.updated_at = past, and the trigger fires.
    await update_message_status(db, msg_id, "classified")

    row_after = await get_message_by_id(db, msg_id)
    assert row_after is not None
    assert row_after["status"] == "classified"
    assert row_after["updated_at"] != past


# ---------------------------------------------------------------------------
# Text / URL / reply pipeline — handle_update with Telegram client mocked
# ---------------------------------------------------------------------------


async def test_handle_plain_text(db: aiosqlite.Connection) -> None:
    update, raw = _make_update(message_id=2001, text="This is a plain message.")

    with patch(
        "app.services.message_intake.send_message", new=AsyncMock()
    ) as mock_send:
        await handle_update(update, db, raw_payload=raw)

    mock_send.assert_awaited_once()
    call_chat_id = mock_send.call_args[0][0]
    assert call_chat_id == 100

    cursor = await db.execute(
        "SELECT * FROM messages WHERE telegram_message_id = ?", (2001,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["message_type"] == "text"
    assert row["raw_payload"] == raw


async def test_handle_url_message(db: aiosqlite.Connection) -> None:
    url = "https://arxiv.org/abs/2301.07041"
    update, raw = _make_update(message_id=2002, text=f"New paper: {url}")

    with patch("app.services.message_intake.send_message", new=AsyncMock()):
        await handle_update(update, db, raw_payload=raw)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE telegram_message_id = ?", (2002,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["message_type"] == "url"
    assert bool(row["has_url"]) is True
    assert row["source_url"] == url


async def test_handle_reply_message(db: aiosqlite.Connection) -> None:
    update, raw = _make_update(message_id=2003, text="Good point!", reply_to_id=42)

    with patch("app.services.message_intake.send_message", new=AsyncMock()):
        await handle_update(update, db, raw_payload=raw)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE telegram_message_id = ?", (2003,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["message_type"] == "reply"
    assert bool(row["is_reply"]) is True
    assert row["reply_to_telegram_id"] == 42


async def test_handle_bare_help_routes_to_operator(db: aiosqlite.Connection) -> None:
    update, raw = _make_update(message_id=2004, text="help")

    with patch(
        "app.services.message_intake.send_message", new=AsyncMock()
    ) as mock_send:
        await handle_update(update, db, raw_payload=raw)

    mock_send.assert_awaited_once()
    assert "Velveteen Operator" in mock_send.call_args[0][1]


async def test_handle_empty_update_is_noop(db: aiosqlite.Connection) -> None:
    update = TelegramUpdate(update_id=999)  # no message
    with patch("app.services.message_intake.send_message", new=AsyncMock()) as mock:
        await handle_update(update, db, raw_payload="{}")
    mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Voice pipeline — Phase 3
# ---------------------------------------------------------------------------


async def test_voice_no_transcriber(db: aiosqlite.Connection) -> None:
    """When OPENAI_API_KEY is absent, voice is persisted with transcription=None."""
    update, raw = _make_voice_update(message_id=4001)

    with patch("app.services.message_intake.send_message", new=AsyncMock()):
        await handle_update(update, db, raw_payload=raw)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE telegram_message_id = ?", (4001,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["message_type"] == "voice"
    assert row["voice_file_id"] == "voice_file_id_123"
    assert row["transcription"] is None


async def test_voice_with_mocked_transcription(db: aiosqlite.Connection) -> None:
    """Voice message is persisted with transcription when transcriber is available."""
    expected = "Idea sobre un pipeline de detección de anomalías en series de tiempo"

    @asynccontextmanager
    async def _fake_download(_file_id: str):
        yield "/tmp/fake_audio.ogg"

    class _FakeTranscriber:
        async def transcribe(self, _path: str) -> str:
            return expected

    update, raw = _make_voice_update(message_id=4002)

    with (
        patch("app.services.message_intake.download_voice", new=_fake_download),
        patch(
            "app.services.message_intake.get_transcriber",
            return_value=_FakeTranscriber(),
        ),
        patch("app.services.message_intake.send_message", new=AsyncMock()),
    ):
        await handle_update(update, db, raw_payload=raw)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE telegram_message_id = ?", (4002,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["message_type"] == "voice"
    assert row["transcription"] == expected


async def test_voice_channel_assigned_from_transcription(
    db: aiosqlite.Connection,
) -> None:
    """Channel is assigned from transcription text when initial text is absent."""
    # Contains "thesis" → should resolve to "research"
    transcription_text = "Avancé en la metodología de mi thesis sobre gradient descent"

    @asynccontextmanager
    async def _fake_download(_file_id: str):
        yield "/tmp/fake.ogg"

    class _FakeTranscriber:
        async def transcribe(self, _path: str) -> str:
            return transcription_text

    update, raw = _make_voice_update(message_id=4003)

    with (
        patch("app.services.message_intake.download_voice", new=_fake_download),
        patch(
            "app.services.message_intake.get_transcriber",
            return_value=_FakeTranscriber(),
        ),
        patch("app.services.message_intake.send_message", new=AsyncMock()),
    ):
        await handle_update(update, db, raw_payload=raw)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE telegram_message_id = ?", (4003,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["channel"] == "research"


async def test_transcription_failure_is_nonfatal(db: aiosqlite.Connection) -> None:
    """If the transcriber raises, the message is persisted with transcription=None."""

    @asynccontextmanager
    async def _fake_download(_file_id: str):
        yield "/tmp/fake.ogg"

    class _BrokenTranscriber:
        async def transcribe(self, _path: str) -> str:
            raise RuntimeError("OpenAI service unavailable")

    update, raw = _make_voice_update(message_id=4004)

    with (
        patch("app.services.message_intake.download_voice", new=_fake_download),
        patch(
            "app.services.message_intake.get_transcriber",
            return_value=_BrokenTranscriber(),
        ),
        patch("app.services.message_intake.send_message", new=AsyncMock()),
    ):
        await handle_update(update, db, raw_payload=raw)

    cursor = await db.execute(
        "SELECT * FROM messages WHERE telegram_message_id = ?", (4004,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["message_type"] == "voice"
    assert row["transcription"] is None
