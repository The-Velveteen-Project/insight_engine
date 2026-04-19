from pydantic import BaseModel, Field


class TelegramUser(BaseModel):
    id: int
    is_bot: bool
    first_name: str
    username: str | None = None


class TelegramChat(BaseModel):
    id: int
    type: str
    title: str | None = None
    username: str | None = None


class TelegramVoice(BaseModel):
    file_id: str
    file_unique_id: str
    duration: int
    mime_type: str | None = None
    file_size: int | None = None


class TelegramDocument(BaseModel):
    file_id: str
    file_unique_id: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None


class TelegramMessage(BaseModel):
    message_id: int
    from_: TelegramUser | None = Field(None, alias="from")
    chat: TelegramChat
    date: int
    text: str | None = None
    voice: TelegramVoice | None = None
    document: TelegramDocument | None = None
    caption: str | None = None
    # Full Telegram message object for the parent; parsed recursively by Pydantic.
    reply_to_message: "TelegramMessage | None" = None

    model_config = {"populate_by_name": True}


# Required for Pydantic v2 to resolve the self-referential annotation.
TelegramMessage.model_rebuild()


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None
    edited_message: TelegramMessage | None = None
