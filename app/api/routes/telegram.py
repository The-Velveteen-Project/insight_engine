from typing import Annotated

import aiosqlite
from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import verify_telegram_secret
from app.db.session import get_db
from app.schemas.telegram import TelegramUpdate
from app.services.message_intake import handle_update

router = APIRouter(tags=["telegram"])


@router.post(
    "/webhook",
    dependencies=[Depends(verify_telegram_secret)],
    status_code=200,
)
async def telegram_webhook(
    request: Request,
    update: TelegramUpdate,
    db: Annotated[aiosqlite.Connection, Depends(get_db)],
) -> Response:
    # Capture raw bytes before Pydantic re-serializes — preserves unknown fields
    # and unknown future Telegram schema additions for debugging.
    raw_payload = (await request.body()).decode()
    await handle_update(update, db, raw_payload=raw_payload)
    return Response(status_code=200)
