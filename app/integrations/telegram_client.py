import contextlib
import os
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.config import settings


def _base() -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}"


async def send_message(
    chat_id: int,
    text: str,
    parse_mode: str = "HTML",
) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{_base()}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10.0,
        )
        response.raise_for_status()


async def get_file(file_id: str) -> dict[str, Any]:
    """Returns the file metadata object from Telegram. Use file_path to download."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{_base()}/getFile",
            params={"file_id": file_id},
            timeout=10.0,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Telegram getFile returned a non-object payload.")
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getFile failed: {data}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Telegram getFile returned no result payload.")
        return result


@asynccontextmanager
async def download_voice(file_id: str) -> AsyncGenerator[str, None]:
    """
    Resolves a Telegram file_id, downloads the audio to a temporary file,
    and yields its local path. Cleans up on exit regardless of errors.

    Telegram voice notes are OGG/Opus — supported directly by Whisper.
    Phase 3 loads the full response into memory; acceptable for voice notes
    (Telegram cap: ~20 MB, typical note: < 2 MB).

    Raises:
        RuntimeError: if Telegram returns no file_path.
        httpx.HTTPStatusError: if the download request fails.
    """
    file_meta = await get_file(file_id)
    file_path = file_meta.get("file_path", "")
    if not file_path:
        raise RuntimeError(f"Telegram returned no file_path for file_id={file_id!r}")

    url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"

    tmp = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
        with open(tmp_path, "wb") as f:
            f.write(response.content)
        yield tmp_path
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
