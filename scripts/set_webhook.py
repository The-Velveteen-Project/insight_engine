"""Register this server's URL as the Telegram webhook."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from app.core.config import settings


async def main() -> None:
    webhook_url = input(
        "Public webhook URL (e.g. https://abc.ngrok.io/api/v1/telegram/webhook): "
    ).strip()

    if not webhook_url.startswith("https://"):
        print("Error: Telegram requires HTTPS. Aborting.")
        return

    payload: dict = {"url": webhook_url}
    if settings.telegram_webhook_secret:
        payload["secret_token"] = settings.telegram_webhook_secret

    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    async with httpx.AsyncClient() as client:
        response = await client.post(f"{base}/setWebhook", json=payload, timeout=10.0)
        data = response.json()

    if data.get("ok"):
        print(f"Webhook registered: {webhook_url}")
        if settings.telegram_webhook_secret:
            print("Secret token: configured.")
        else:
            print(
                "Warning: no TELEGRAM_WEBHOOK_SECRET set"
                " — webhook is unauthenticated."
            )
    else:
        print(f"Telegram API error: {data}")


if __name__ == "__main__":
    asyncio.run(main())
