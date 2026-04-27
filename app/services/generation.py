"""
Structured generation service for Phase 6.

The generator is optional. If the model is unavailable or fails, callers
must fall back to deterministic output.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.core.config import settings
from app.integrations.openai_compat import build_async_openai_client
from app.prompts.drafts import DRAFT_SYSTEM_PROMPT, build_draft_prompt
from app.prompts.editorial import (
    EDITORIAL_SYSTEM_PROMPT,
    WEEKLY_THESIS_SYSTEM_PROMPT,
    build_editorial_prompt,
    build_weekly_thesis_prompt,
)
from app.prompts.handoff_match import (
    HANDOFF_MATCH_SYSTEM_PROMPT,
    build_handoff_match_prompt,
)
from app.schemas.drafts import DraftGenerationInput, EditorialDraftContent
from app.schemas.editorial import (
    EditorialGenerationInput,
    GeneratedEditorialDraft,
    WeeklyThesis,
    WeeklyThesisGenerationInput,
)
from app.schemas.goals import HandoffMatchInput, HandoffRepoMatch

logger = logging.getLogger(__name__)


@runtime_checkable
class StructuredEditorialGenerator(Protocol):
    async def generate(
        self,
        context: EditorialGenerationInput,
    ) -> GeneratedEditorialDraft | None: ...


@runtime_checkable
class StructuredDraftGenerator(Protocol):
    async def generate(
        self,
        context: DraftGenerationInput,
    ) -> EditorialDraftContent | None: ...


@runtime_checkable
class StructuredWeeklyThesisGenerator(Protocol):
    async def generate(
        self,
        context: WeeklyThesisGenerationInput,
    ) -> WeeklyThesis | None: ...


class OpenAIEditorialGenerator:
    """
    Structured editorial generation via the Responses API.

    Uses Pydantic-based structured outputs so the narrative fields arrive
    already validated at the integration boundary.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._client = build_async_openai_client(api_key=api_key)
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


class OpenAIDraftGenerator:
    """
    Structured draft generation via the Responses API.

    Keeps the same integration boundary as editorial plan generation:
    validated Pydantic output or None on failure.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._client = build_async_openai_client(api_key=api_key)
        self._model = model

    async def _parse_structured_response(
        self,
        context: DraftGenerationInput,
    ) -> EditorialDraftContent | None:
        response = await self._client.responses.parse(
            model=self._model,
            instructions=DRAFT_SYSTEM_PROMPT,
            input=build_draft_prompt(context),
            text_format=EditorialDraftContent,
            max_output_tokens=900,
            temperature=0.2,
        )
        parsed = getattr(response, "output_parsed", None)
        if not isinstance(parsed, EditorialDraftContent):
            logger.warning("Draft generation returned no structured output.")
            return None
        return parsed

    async def generate(
        self,
        context: DraftGenerationInput,
    ) -> EditorialDraftContent | None:
        try:
            return await self._parse_structured_response(context)
        except Exception as exc:
            logger.warning("Draft generation failed: %s", exc)
            return None


class OpenAIWeeklyThesisGenerator:
    """
    Structured weekly-thesis generation via the Responses API.

    Produces the opening paragraph of the weekly digest and the proactive
    handoff flag, using the same Pydantic-structured-output pattern as the
    editorial generator. Returns None on failure so the caller can fall back
    to a deterministic synthesis.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self._client = build_async_openai_client(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
        self._model = model

    async def _parse_structured_response(
        self,
        context: WeeklyThesisGenerationInput,
    ) -> WeeklyThesis | None:
        response = await self._client.responses.parse(
            model=self._model,
            instructions=WEEKLY_THESIS_SYSTEM_PROMPT,
            input=build_weekly_thesis_prompt(context),
            text_format=WeeklyThesis,
            max_output_tokens=600,
            temperature=0.3,
        )
        parsed = getattr(response, "output_parsed", None)
        if not isinstance(parsed, WeeklyThesis):
            logger.warning("Weekly thesis generation returned no structured output.")
            return None
        return parsed

    async def generate(
        self,
        context: WeeklyThesisGenerationInput,
    ) -> WeeklyThesis | None:
        try:
            return await self._parse_structured_response(context)
        except Exception as exc:
            logger.warning("Weekly thesis generation failed: %s", exc)
            return None


class OpenAIHandoffMatcher:
    """Structured plan↔repo match judgment for handoff follow-ups."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self._client = build_async_openai_client(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )
        self._model = model

    async def _parse_structured_response(
        self,
        context: HandoffMatchInput,
    ) -> HandoffRepoMatch | None:
        response = await self._client.responses.parse(
            model=self._model,
            instructions=HANDOFF_MATCH_SYSTEM_PROMPT,
            input=build_handoff_match_prompt(context),
            text_format=HandoffRepoMatch,
            max_output_tokens=400,
            temperature=0.1,
        )
        parsed = getattr(response, "output_parsed", None)
        if not isinstance(parsed, HandoffRepoMatch):
            logger.warning("Handoff match returned no structured output.")
            return None
        return parsed

    async def generate(
        self,
        context: HandoffMatchInput,
    ) -> HandoffRepoMatch | None:
        try:
            return await self._parse_structured_response(context)
        except Exception as exc:
            logger.warning("Handoff match generation failed: %s", exc)
            return None


_generator: OpenAIEditorialGenerator | None = None
_draft_generator: OpenAIDraftGenerator | None = None
_weekly_thesis_generator: OpenAIWeeklyThesisGenerator | None = None
_handoff_matcher: OpenAIHandoffMatcher | None = None


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


def get_draft_generator() -> OpenAIDraftGenerator | None:
    global _draft_generator
    if _draft_generator is None and settings.openai_api_key:
        _draft_generator = OpenAIDraftGenerator(
            api_key=settings.openai_api_key,
            model=settings.editorial_model,
        )
        logger.info(
            "OpenAIDraftGenerator initialized (model=%s).",
            settings.editorial_model,
        )
    return _draft_generator


def get_weekly_thesis_generator() -> OpenAIWeeklyThesisGenerator | None:
    global _weekly_thesis_generator
    if (
        _weekly_thesis_generator is None
        and settings.openai_api_key
        and settings.weekly_use_llm_thesis
    ):
        _weekly_thesis_generator = OpenAIWeeklyThesisGenerator(
            api_key=settings.openai_api_key,
            model=settings.editorial_model,
            timeout_seconds=settings.weekly_thesis_timeout_seconds,
        )
        logger.info(
            "OpenAIWeeklyThesisGenerator initialized (model=%s).",
            settings.editorial_model,
        )
    return _weekly_thesis_generator


def get_handoff_matcher() -> OpenAIHandoffMatcher | None:
    global _handoff_matcher
    if _handoff_matcher is None and settings.openai_api_key:
        _handoff_matcher = OpenAIHandoffMatcher(
            api_key=settings.openai_api_key,
            model=settings.editorial_model,
            timeout_seconds=settings.handoff_match_timeout_seconds,
        )
        logger.info(
            "OpenAIHandoffMatcher initialized (model=%s).",
            settings.editorial_model,
        )
    return _handoff_matcher
