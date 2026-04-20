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

from app.core.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a search query normalizer for academic and tech APIs "
    "(arXiv, Hacker News). "
    "Translate the user's query to English. "
    "If it is already in English, return it unchanged. "
    "Keep the output concise: 2–6 words, no punctuation, no explanation. "
    "Return ONLY the normalized query."
)


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
        normalized = message.content[0].text.strip().strip('"').strip("'")
        if normalized and normalized.lower() != query.strip().lower():
            logger.info("Query normalized: %r → %r", query, normalized)
        return normalized or query
    except Exception as exc:
        logger.warning("Query normalization failed for %r: %s", query, exc)
        return query
