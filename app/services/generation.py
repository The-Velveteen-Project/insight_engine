"""
Structured generation service for Phase 6.

The generator is optional. If the model is unavailable or fails, callers
must fall back to deterministic output.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Protocol, TypeVar, runtime_checkable

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel

from app.core.config import settings
from app.integrations.openai_compat import (
    build_async_openai_client,
    completion_params,
)
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
from app.prompts.job_details import JOB_DETAILS_SYSTEM_PROMPT, build_job_details_prompt
from app.schemas.campaign import GeneratedCampaignPlan
from app.schemas.cv import GapAnalysis, TailoredCV
from app.schemas.drafts import DraftGenerationInput, EditorialDraftContent
from app.schemas.editorial import (
    EditorialGenerationInput,
    GeneratedEditorialDraft,
    WeeklyThesis,
    WeeklyThesisGenerationInput,
)
from app.schemas.goals import HandoffMatchInput, HandoffRepoMatch
from app.schemas.jobs import JobPostingDetails

logger = logging.getLogger(__name__)

_BM = TypeVar("_BM", bound=BaseModel)


async def _structured_completion(
    client: AsyncOpenAI,
    *,
    model: str,
    system: str,
    user: str,
    response_cls: type[_BM],
    max_tokens: int,
    temperature: float,
) -> _BM | None:
    """Provider-agnostic structured LLM call with two-tier fallback.

    Tier 1 — beta.chat.completions.parse():
        Uses OpenAI-native json_schema structured outputs. Works natively on
        OpenAI. Some OpenRouter models support it too (those that implement the
        json_schema response_format spec).

    Tier 2 — chat.completions.create() with response_format json_object:
        Universally supported by OpenRouter, Groq, Ollama, and almost any
        provider. We embed the Pydantic JSON schema in the system prompt and
        parse the raw JSON content manually with Pydantic.

    Only if both tiers fail do we return None, at which point callers must
    use their deterministic fallback.
    """
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Tier 1: structured outputs (preferred — schema-validated by the provider)
    try:
        resp = await client.beta.chat.completions.parse(
            model=model,
            messages=messages,
            response_format=response_cls,
            **completion_params(model, max_tokens=max_tokens, temperature=temperature),
        )
        parsed = resp.choices[0].message.parsed if resp.choices else None
        if isinstance(parsed, response_cls):
            logger.debug("Structured output (parse) succeeded for model=%r.", model)
            return parsed
    except Exception as exc:
        logger.debug(
            "Structured output (parse) unavailable for model=%r, "
            "falling back to json_object mode: %s",
            model,
            exc,
        )

    # Tier 2: json_object mode — inject schema into system prompt
    schema = _json.dumps(
        response_cls.model_json_schema(), ensure_ascii=False, separators=(",", ":")
    )
    augmented_system = (
        f"{system}\n\n"
        "Respond with ONLY a valid JSON object that exactly matches this "
        f"schema (no markdown, no extra text):\n{schema}"
    )
    try:
        resp2 = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": augmented_system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            **completion_params(model, max_tokens=max_tokens, temperature=temperature),
        )
        content = resp2.choices[0].message.content if resp2.choices else None
        if not content:
            return None
        result = response_cls.model_validate_json(content)
        logger.debug("Structured output (json_object) succeeded for model=%r.", model)
        return result
    except Exception as exc2:
        logger.warning(
            "Structured completion both tiers failed for model=%r: %s",
            model,
            exc2,
        )
        return None


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
    """Structured editorial generation.

    Provider-agnostic via _structured_completion.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._client = build_async_openai_client(api_key=api_key)
        self._model = model

    async def generate(
        self,
        context: EditorialGenerationInput,
    ) -> GeneratedEditorialDraft | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=EDITORIAL_SYSTEM_PROMPT,
            user=build_editorial_prompt(context),
            response_cls=GeneratedEditorialDraft,
            max_tokens=700,
            temperature=0.2,
        )


class OpenAIDraftGenerator:
    """Structured draft generation.

    Provider-agnostic via _structured_completion.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._client = build_async_openai_client(api_key=api_key)
        self._model = model

    async def generate(
        self,
        context: DraftGenerationInput,
    ) -> EditorialDraftContent | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=DRAFT_SYSTEM_PROMPT,
            user=build_draft_prompt(context),
            response_cls=EditorialDraftContent,
            max_tokens=900,
            temperature=0.2,
        )


class OpenAIWeeklyThesisGenerator:
    """Structured weekly-thesis generation.

    Provider-agnostic via _structured_completion.
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

    async def generate(
        self,
        context: WeeklyThesisGenerationInput,
    ) -> WeeklyThesis | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=WEEKLY_THESIS_SYSTEM_PROMPT,
            user=build_weekly_thesis_prompt(context),
            response_cls=WeeklyThesis,
            max_tokens=600,
            temperature=0.3,
        )


class OpenAIHandoffMatcher:
    """Structured plan↔repo match judgment.

    Provider-agnostic via _structured_completion.
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

    async def generate(
        self,
        context: HandoffMatchInput,
    ) -> HandoffRepoMatch | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=HANDOFF_MATCH_SYSTEM_PROMPT,
            user=build_handoff_match_prompt(context),
            response_cls=HandoffRepoMatch,
            max_tokens=400,
            temperature=0.1,
        )


class OpenAIJobDetailsExtractor:
    """Posting text -> JobPostingDetails. Extraction only, no judgment."""

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

    async def generate(
        self, *, title: str, url: str, text: str
    ) -> JobPostingDetails | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=JOB_DETAILS_SYSTEM_PROMPT,
            user=build_job_details_prompt(title=title, url=url, text=text),
            response_cls=JobPostingDetails,
            max_tokens=900,
            temperature=0,
        )


class OpenAIGapAnalyst:
    """Master CV + posting -> GapAnalysis (judgment over supplied facts)."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float) -> None:
        self._client = build_async_openai_client(
            api_key=api_key, timeout_seconds=timeout_seconds
        )
        self._model = model

    async def generate(self, *, system: str, user: str) -> GapAnalysis | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=system,
            user=user,
            response_cls=GapAnalysis,
            max_tokens=1400,
            temperature=0.2,
        )


class OpenAICVWriter:
    """Master CV + posting -> TailoredCV (selection and rephrasing only)."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float) -> None:
        self._client = build_async_openai_client(
            api_key=api_key, timeout_seconds=timeout_seconds
        )
        self._model = model

    async def generate(self, *, system: str, user: str) -> TailoredCV | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=system,
            user=user,
            response_cls=TailoredCV,
            max_tokens=2600,
            temperature=0.3,
        )


class OpenAICampaignPlanner:
    """Gap analysis -> four-week plan (GeneratedCampaignPlan)."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float) -> None:
        self._client = build_async_openai_client(
            api_key=api_key, timeout_seconds=timeout_seconds
        )
        self._model = model

    async def generate(self, *, system: str, user: str) -> GeneratedCampaignPlan | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=system,
            user=user,
            response_cls=GeneratedCampaignPlan,
            max_tokens=1600,
            temperature=0.3,
        )


_campaign_planner: OpenAICampaignPlanner | None = None


def get_campaign_planner() -> OpenAICampaignPlanner | None:
    global _campaign_planner
    if _campaign_planner is None and settings.openai_api_key:
        _campaign_planner = OpenAICampaignPlanner(
            api_key=settings.openai_api_key,
            model=settings.editorial_model,
            timeout_seconds=settings.cv_timeout_seconds,
        )
    return _campaign_planner


_gap_analyst: OpenAIGapAnalyst | None = None
_cv_writer: OpenAICVWriter | None = None


def get_gap_analyst() -> OpenAIGapAnalyst | None:
    global _gap_analyst
    if _gap_analyst is None and settings.openai_api_key:
        _gap_analyst = OpenAIGapAnalyst(
            api_key=settings.openai_api_key,
            model=settings.editorial_model,
            timeout_seconds=settings.cv_timeout_seconds,
        )
    return _gap_analyst


def get_cv_writer() -> OpenAICVWriter | None:
    global _cv_writer
    if _cv_writer is None and settings.openai_api_key:
        _cv_writer = OpenAICVWriter(
            api_key=settings.openai_api_key,
            model=settings.editorial_model,
            timeout_seconds=settings.cv_timeout_seconds,
        )
    return _cv_writer


_job_details_extractor: OpenAIJobDetailsExtractor | None = None


def get_job_details_extractor() -> OpenAIJobDetailsExtractor | None:
    global _job_details_extractor
    if _job_details_extractor is None and settings.openai_api_key:
        _job_details_extractor = OpenAIJobDetailsExtractor(
            api_key=settings.openai_api_key,
            model=settings.resolved_utility_model,
            timeout_seconds=settings.job_details_timeout_seconds,
        )
        logger.info(
            "OpenAIJobDetailsExtractor initialized (model=%s).",
            settings.resolved_utility_model,
        )
    return _job_details_extractor


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
