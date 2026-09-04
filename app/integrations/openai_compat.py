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

from typing import TYPE_CHECKING, TypedDict

from app.core.config import settings

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def build_async_openai_client(
    *,
    api_key: str,
    timeout_seconds: float | None = None,
) -> AsyncOpenAI:
    """Construct AsyncOpenAI honoring settings.openai_base_url when set."""
    from openai import AsyncOpenAI

    # Always pass base_url explicitly. The SDK reads OPENAI_BASE_URL from the
    # environment when base_url is None, and an *empty* env value (common in
    # hosted dashboards) becomes an empty URL that breaks every request.
    base_url = settings.openai_base_url.strip() or _OPENAI_DEFAULT_BASE_URL
    kwargs: dict[str, object] = {"api_key": api_key, "base_url": base_url}
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    return AsyncOpenAI(**kwargs)  # type: ignore[arg-type]


# Reasoning-era models (gpt-5.x, o-series) reject `max_tokens` and any
# `temperature` other than the default. Older chat models accept both names.
_FIXED_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")

# Reasoning tokens are billed against max_completion_tokens. With a long
# prompt (master CV + posting) gpt-5.6 spent the whole 1400-token budget
# thinking and returned nothing, so reasoning models get extra headroom.
_REASONING_TOKEN_RESERVE = 8000


def is_reasoning_model(model: str) -> bool:
    return model.split("/")[-1].lower().startswith(_FIXED_TEMPERATURE_PREFIXES)


class CompletionParams(TypedDict, total=False):
    max_completion_tokens: int
    temperature: float


def completion_params(
    model: str,
    *,
    max_tokens: int,
    temperature: float | None = None,
) -> CompletionParams:
    """Token-limit and sampling kwargs that every current OpenAI model accepts.

    `max_completion_tokens` is understood by the whole chat lineup; `max_tokens`
    is rejected by gpt-5.x. Temperature is only sent to models that honor it.
    """
    reasoning = is_reasoning_model(model)
    budget = max_tokens + _REASONING_TOKEN_RESERVE if reasoning else max_tokens
    params: CompletionParams = {"max_completion_tokens": budget}
    if temperature is not None and not reasoning:
        params["temperature"] = temperature
    return params
