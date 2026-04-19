from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Message:
    # Telegram identifiers
    telegram_message_id: int
    telegram_chat_id: int
    raw_payload: str

    # Sender
    user_id: int | None = None
    username: str | None = None

    # Content
    text: str | None = None
    source_url: str | None = None
    voice_file_id: str | None = None
    voice_duration: int | None = None
    transcription: str | None = None
    reply_to_telegram_id: int | None = None

    # Orthogonal flags — preserved independently of message_type priority
    has_url: bool = False
    is_reply: bool = False

    # message_type: structural type of the event (text/voice/url/reply/document)
    # channel: editorial lane of the content (signal/build/research/teaching/philosophy)
    message_type: str = "text"
    channel: str | None = None

    # Lifecycle
    status: str = "received"
    id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    processed_at: datetime | None = None
