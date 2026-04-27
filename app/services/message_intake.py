import logging

import aiosqlite

from app.db.queries import insert_message
from app.domain.message import Message
from app.integrations.telegram_client import download_voice, send_message
from app.schemas.common import MessageType
from app.schemas.telegram import TelegramMessage, TelegramUpdate
from app.services.classifier import MessageClassification, classify, classify_channel
from app.services.telegram_orchestrator import handle_operator_text
from app.services.transcription import get_transcriber

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Acknowledgment
# ---------------------------------------------------------------------------


def _ack(
    c: MessageClassification,
    msg: TelegramMessage,
    transcription: str | None,
) -> str:
    channel_tag = f" [{c.channel}]" if c.channel else ""

    if c.message_type == MessageType.VOICE:
        if transcription:
            preview = transcription[:80] + ("…" if len(transcription) > 80 else "")
            return f"<b>Voice{channel_tag}</b>\n<code>{preview}</code>"
        duration = msg.voice.duration if msg.voice else 0
        return f"<b>Voice{channel_tag}</b> ({duration}s) — transcription unavailable."

    if c.message_type == MessageType.URL:
        url_preview = (c.source_url or "")[:60]
        return f"<b>URL{channel_tag}</b>\n<code>{url_preview}</code>"

    if c.message_type == MessageType.REPLY:
        return f"<b>Reply{channel_tag}</b> → #{c.reply_to_telegram_id}"

    if c.message_type == MessageType.DOCUMENT:
        name = msg.document.file_name if msg.document else "file"
        return f"<b>Document{channel_tag}:</b> {name}"

    text = msg.text or ""
    preview = text[:60] + ("…" if len(text) > 60 else "")
    return f"<b>Received{channel_tag}</b>\n<code>{preview}</code>"


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


async def _attempt_transcription(voice_file_id: str) -> str | None:
    """
    Downloads and transcribes a Telegram voice note.

    Non-fatal: returns None on any failure so the intake pipeline always
    completes. Failures are logged at ERROR level for post-hoc debugging.
    """
    transcriber = get_transcriber()
    if transcriber is None:
        logger.warning(
            "Transcription skipped for file_id=%r: OPENAI_API_KEY not configured.",
            voice_file_id,
        )
        return None

    try:
        async with download_voice(voice_file_id) as audio_path:
            result = await transcriber.transcribe(audio_path)
        logger.info(
            "Voice note transcribed: file_id=%r, chars=%d.",
            voice_file_id,
            len(result),
        )
        return result
    except Exception as exc:
        logger.error(
            "Transcription failed for file_id=%r: %s",
            voice_file_id,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def handle_update(
    update: TelegramUpdate,
    db: aiosqlite.Connection,
    raw_payload: str,
) -> None:
    """
    Entry point for all incoming Telegram updates.

    Unsupported update types (callback_query, inline_query, etc.) are
    discarded here — Pydantic already strips unknown fields, so any update
    without a message or edited_message resolves to msg=None.
    """
    msg = update.message or update.edited_message
    if not msg:
        logger.debug(
            "Ignored update_id=%d: no processable message payload.",
            update.update_id,
        )
        return

    c = classify(msg)
    transcription: str | None = None

    if c.message_type == MessageType.VOICE and msg.voice:
        transcription = await _attempt_transcription(msg.voice.file_id)
        # Re-run channel assignment on transcribed text when initial text
        # classification returned None (voice notes have no typed text).
        if transcription and c.channel is None:
            c.channel = classify_channel(transcription.lower())

    domain_msg = Message(
        telegram_message_id=msg.message_id,
        telegram_chat_id=msg.chat.id,
        user_id=msg.from_.id if msg.from_ else None,
        username=msg.from_.username if msg.from_ else None,
        # text holds only literal typed content (caption or message text).
        # Transcribed voice content lives exclusively in `transcription`.
        text=msg.text or msg.caption,
        source_url=c.source_url,
        voice_file_id=msg.voice.file_id if msg.voice else None,
        voice_duration=msg.voice.duration if msg.voice else None,
        reply_to_telegram_id=c.reply_to_telegram_id,
        has_url=c.has_url,
        is_reply=c.is_reply,
        message_type=c.message_type,
        channel=c.channel,
        transcription=transcription,
        raw_payload=raw_payload,
    )

    persisted_message_id = await insert_message(db, domain_msg)
    if persisted_message_id is None:
        # Telegram re-delivered a webhook we already processed. Return 200
        # silently so Telegram stops retrying without double-executing.
        logger.debug(
            "Duplicate webhook ignored: chat_id=%d message_id=%d.",
            msg.chat.id,
            msg.message_id,
        )
        return

    incoming_text = msg.text or msg.caption
    if incoming_text:
        response_text = await handle_operator_text(
            incoming_text,
            db,
            message_id=persisted_message_id,
            chat_id=msg.chat.id,
        )
    else:
        response_text = None

    if response_text is not None:
        await send_message(msg.chat.id, response_text)
        return

    await send_message(msg.chat.id, _ack(c, msg, transcription))
