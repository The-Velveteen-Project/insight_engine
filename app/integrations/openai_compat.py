"""
Thin helper that builds an AsyncOpenAI client respecting OPENAI_BASE_URL.

Sub-phase B.7. Lets the editorial / draft / weekly thesis / linkedin /
handoff-match generators run against any OpenAI-compatible endpoint
(OpenAI, OpenRouter, Groq, Together, local Ollama) by setting one env var.

Whisper transcription deliberately uses the OpenAI default (no override),
since Groq/OpenRouter do not all expose a compatible Whisper endpoint and
audio-shape parity is fragile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from openai import AsyncOpenAI


def build_async_openai_client(
    *,
    api_key: str,
    timeout_seconds: float | None = None,
) -> AsyncOpenAI:
    """Construct AsyncOpenAI honoring settings.openai_base_url when set."""
    from openai import AsyncOpenAI

    base_url = settings.openai_base_url.strip() or None
    kwargs: dict[str, object] = {"api_key": api_key}
    if base_url is not None:
        kwargs["base_url"] = base_url
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    return AsyncOpenAI(**kwargs)  # type: ignore[arg-type]
