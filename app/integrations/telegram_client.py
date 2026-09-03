import asyncio
import contextlib
import os
import re
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.config import settings


def _base() -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}"


_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^>]*)?>")

# Room left in every chunk for the closing/reopening tags added by
# _balance_html_chunks. 64 chars covers a nested <pre><code class="..."> pair.
_HTML_BALANCE_RESERVE = 64


def _balance_html_chunks(chunks: list[str]) -> list[str]:
    """Close tags left open at a chunk boundary and reopen them in the next.

    Telegram parses each message independently, so a <pre> block that spans
    two chunks makes both of them invalid HTML (400 "can't parse entities").
    Content inside <pre>/<code> is already escaped by the formatters, so the
    only `<` characters present are real tags.
    """
    balanced: list[str] = []
    open_tags: list[tuple[str, str]] = []  # (name, full opening tag)
    for chunk in chunks:
        prefix = "".join(tag for _, tag in open_tags)
        for match in _TAG_RE.finditer(chunk):
            is_closing, name = bool(match.group(1)), match.group(2).lower()
            if is_closing:
                for index in range(len(open_tags) - 1, -1, -1):
                    if open_tags[index][0] == name:
                        del open_tags[index]
                        break
            else:
                open_tags.append((name, match.group(0)))
        suffix = "".join(f"</{name}>" for name, _ in reversed(open_tags))
        balanced.append(f"{prefix}{chunk}{suffix}")
    return balanced


def _message_chunks(text: str, *, limit: int) -> list[str]:
    limit = max(limit, 1)
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    blocks = text.split("\n\n")

    for block in blocks:
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(block) <= limit:
            current = block
            continue

        lines = block.splitlines()
        line_buffer = ""
        for line in lines:
            line_candidate = line if not line_buffer else f"{line_buffer}\n{line}"
            if len(line_candidate) <= limit:
                line_buffer = line_candidate
                continue
            if line_buffer:
                chunks.append(line_buffer)
            line_buffer = line
            while len(line_buffer) > limit:
                chunks.append(line_buffer[:limit])
                line_buffer = line_buffer[limit:]
        if line_buffer:
            current = line_buffer

    if current:
        chunks.append(current)
    return chunks


async def _post_message(
    client: httpx.AsyncClient,
    *,
    chat_id: int,
    text: str,
    parse_mode: str,
) -> None:
    attempts = settings.telegram_send_retries + 1
    for attempt in range(attempts):
        try:
            response = await client.post(
                f"{_base()}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
                timeout=10.0,
            )
            response.raise_for_status()
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or attempt >= attempts - 1:
                raise
            retry_after = 1.0
            with contextlib.suppress(Exception):
                payload = exc.response.json()
                params = payload.get("parameters", {})
                retry_after = float(params.get("retry_after", 1))
            await asyncio.sleep(retry_after)
        except httpx.RequestError:
            if attempt >= attempts - 1:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))


async def send_message(
    chat_id: int,
    text: str,
    parse_mode: str = "HTML",
) -> None:
    transport = httpx.AsyncHTTPTransport(retries=1)
    limit = settings.telegram_max_message_chars
    if len(text) <= limit:
        chunks = [text]
    elif parse_mode == "HTML":
        reserve = min(_HTML_BALANCE_RESERVE, limit // 4)
        chunks = _balance_html_chunks(_message_chunks(text, limit=limit - reserve))
    else:
        chunks = _message_chunks(text, limit=limit)
    async with httpx.AsyncClient(transport=transport) as client:
        for chunk in chunks:
            await _post_message(
                client,
                chat_id=chat_id,
                text=chunk,
                parse_mode=parse_mode,
            )


async def send_document(
    chat_id: int,
    *,
    filename: str,
    content: bytes,
    caption: str | None = None,
    mime_type: str = "text/markdown",
) -> None:
    """Upload a small file to the chat (used for tailored CVs)."""
    data: dict[str, str] = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption[:1024]
        data["parse_mode"] = "HTML"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{_base()}/sendDocument",
            data=data,
            files={"document": (filename, content, mime_type)},
            timeout=30.0,
        )
        response.raise_for_status()


async def download_file(file_id: str, *, max_bytes: int = 2_000_000) -> bytes:
    """Resolve a Telegram file_id and return its bytes (bounded)."""
    file_meta = await get_file(file_id)
    file_path = file_meta.get("file_path", "")
    if not file_path:
        raise RuntimeError(f"Telegram returned no file_path for file_id={file_id!r}")
    url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
    content = response.content
    if len(content) > max_bytes:
        raise RuntimeError("El archivo supera el tamaño máximo permitido.")
    return content


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
