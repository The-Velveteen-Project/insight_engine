from fastapi import Header, HTTPException

from app.core.config import settings


async def verify_telegram_secret(
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> None:
    """Validates the Telegram webhook secret token when configured."""
    if not settings.telegram_webhook_secret:
        return
    if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
