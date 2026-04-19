"""
Structured generation service for Phase 6.

The generator is optional. If the model is unavailable or fails, callers
must fall back to deterministic output.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.core.config import settings
from app.prompts.editorial import EDITORIAL_SYSTEM_PROMPT, build_editorial_prompt
from app.schemas.editorial import EditorialGenerationInput, GeneratedEditorialDraft

logger = logging.getLogger(__name__)


@runtime_checkable
class StructuredEditorialGenerator(Protocol):
    async def generate(
        self,
        context: EditorialGenerationInput,
    ) -> GeneratedEditorialDraft | None: ...


class OpenAIEditorialGenerator:
    """
    Structured editorial generation via the Responses API.

    Uses Pydantic-based structured outputs so the narrative fields arrive
    already validated at the integration boundary.
    """

    def __init__(self, api_key: str, model: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def _parse_structured_response(
        self,
        context: EditorialGenerationInput,
    ) -> GeneratedEditorialDraft | None:
        response = await self._client.responses.parse(
            model=self._model,
            instructions=EDITORIAL_SYSTEM_PROMPT,
            input=build_editorial_prompt(context),
            text_format=GeneratedEditorialDraft,
            max_output_tokens=700,
            temperature=0.2,
        )
        parsed = getattr(response, "output_parsed", None)
        if not isinstance(parsed, GeneratedEditorialDraft):
            logger.warning("Editorial generation returned no structured output.")
            return None
        return parsed

    async def generate(
        self,
        context: EditorialGenerationInput,
    ) -> GeneratedEditorialDraft | None:
        try:
            return await self._parse_structured_response(context)
        except Exception as exc:
            logger.warning("Editorial generation failed: %s", exc)
            return None


_generator: OpenAIEditorialGenerator | None = None


def get_editorial_generator() -> OpenAIEditorialGenerator | None:
    global _generator
    if _generator is None and settings.openai_api_key:
        _generator = OpenAIEditorialGenerator(
            api_key=settings.openai_api_key,
            model=settings.editorial_model,
        )
        logger.info(
            "OpenAIEditorialGenerator initialized (model=%s).",
            settings.editorial_model,
        )
    return _generator
