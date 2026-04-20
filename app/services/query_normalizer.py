"""
Query normalizer — translates search queries to English for external APIs.

Uses Claude Haiku when ANTHROPIC_API_KEY is configured.
Falls back to the original query silently on any error or missing key.

Design constraints:
- Non-fatal: caller always receives a usable string.
- Deferred import: `anthropic` is only imported at call time to avoid
  startup cost when the key is absent.
- No caching: queries are short and the cost per call is negligible.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a search query normalizer for academic and tech APIs "
    "(arXiv, Hacker News). "
    "Translate the user's query to English for retrieval. "
    "If it is already in English, return it unchanged. "
    "Preserve domain-specific nouns and technical qualifiers. "
    "Do not broaden the topic. "
    "Return one concise search query, ideally 2-8 words, no punctuation, "
    "no explanation. "
    "Return ONLY the normalized query."
)


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return ""

    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        text = getattr(block, "text", "")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


async def normalize(query: str) -> str:
    """
    Return an English-normalized version of `query`.

    Uses Claude Haiku when configured.
    Falls back to the original query on any error or if key not set.
    """
    if not settings.anthropic_api_key or not query.strip():
        return query

    try:
        from anthropic import AsyncAnthropic  # deferred import

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        message = await client.messages.create(
            model=settings.normalizer_model,
            max_tokens=32,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query.strip()}],
        )
        normalized = _extract_text(message).strip('"').strip("'")
        if normalized and normalized.lower() != query.strip().lower():
            logger.info("Query normalized: %r → %r", query, normalized)
        return normalized or query
    except Exception as exc:
        logger.warning("Query normalization failed for %r: %s", query, exc)
        return query
