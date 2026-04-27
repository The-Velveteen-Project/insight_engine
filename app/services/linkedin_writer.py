"""
LinkedIn writer service (Sub-phase B.5).

Produces a paste-ready LinkedIn post (Option A: a fresh LLM call with a
LinkedIn-specific prompt, not a reformat of the draft pipeline) and a
portable prompt kit Carlos can paste into another LLM if he wants to
iterate himself.

Falls back to a deterministic post when the LLM is unavailable. The
fallback is sober and short — clearly a placeholder that says "armé
algo basado en el plan, revísalo y reescríbelo".
"""

from __future__ import annotations

import logging

import aiosqlite

from app.core.config import settings
from app.db.queries import get_signals_by_ids
from app.integrations.openai_compat import build_async_openai_client
from app.services.generation import _structured_completion
from app.prompts.linkedin import (
    LINKEDIN_SYSTEM_PROMPT,
    build_linkedin_prompt_kit_text,
    build_linkedin_user_prompt,
)
from app.schemas.editorial import EditorialSignalContext
from app.schemas.linkedin import (
    LinkedInPost,
    LinkedInPostInput,
    LinkedInPromptKit,
)
from app.services import active_goals
from app.services.editorial_planner import get_persisted_editorial_plan

logger = logging.getLogger(__name__)


def _row_to_signal_context(row: aiosqlite.Row) -> EditorialSignalContext:
    return EditorialSignalContext(
        id=int(row["id"]),
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]) if row["source_id"] is not None else None,
        title=str(row["title"] or ""),
        summary=str(row["summary"] or ""),
        url=str(row["url"]) if row["url"] is not None else None,
        relevance_score=float(row["relevance_score"] or 0.0),
        relevance_note=str(row["relevance_note"] or ""),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
    )


async def _build_input(
    db: aiosqlite.Connection,
    plan_id: int,
) -> LinkedInPostInput:
    plan = await get_persisted_editorial_plan(db, plan_id)
    proposal = plan.proposal
    rows = await get_signals_by_ids(db, proposal.signal_ids)
    signal_contexts = [_row_to_signal_context(row) for row in rows]
    if not signal_contexts:
        raise LookupError(
            f"Cannot build LinkedIn post for plan {plan_id}: no persisted signals."
        )

    current_goal = await active_goals.get_current(db)
    active_goal_text = current_goal.label if current_goal is not None else None

    return LinkedInPostInput(
        plan_id=plan.plan_id,
        recommended_action=proposal.recommended_action,
        angle=proposal.angle,
        why_it_matters=proposal.why_it_matters,
        portfolio_value=proposal.portfolio_value,
        draft_hook=proposal.draft_outline.hook,
        draft_points=proposal.draft_outline.points,
        draft_closing=proposal.draft_outline.closing,
        signals=signal_contexts,
        active_goal=active_goal_text,
    )


def _fallback_post(context: LinkedInPostInput) -> LinkedInPost:
    """Honest skeleton post used when the LLM is unavailable.

    The previous version stitched the editorial templates directly into the
    body, which produced text like "Cerrar con una implicación..." literal —
    those strings are *instructions* to a draft writer, not content. This
    version returns a single coherent skeleton that flags itself as a draft
    so Carlos rewrites it before publishing instead of pasting it as-is.
    """
    primary = context.signals[0]
    title_clip = primary.title.strip()
    if len(title_clip) > 120:
        title_clip = title_clip[:117].rsplit(" ", 1)[0]
    angle_lower = context.angle.lower()
    sources_label = (
        "tu repo"
        if primary.source_type == "github"
        else "una pieza externa que vi esta semana"
    )

    hook = (
        f"Borrador (escríbelo tú): "
        f"esta semana volví sobre {sources_label} y quiero dejar una nota "
        "corta antes de que se diluya."
    )[:240]

    main = (
        f"La línea es: {title_clip}. "
        f"Lo que me interesa es el ángulo concreto — "
        f"{context.angle.rstrip('.').lower()}."
    )

    grounded = (
        f"Por qué importa para mí ahora: {context.why_it_matters.rstrip('.')}. "
        "No estoy todavía en posición de afirmar más que eso; lo dejo aquí "
        "porque la línea me parece defendible."
    )

    body_paragraphs = [main, grounded]
    closing = (
        "¿Qué señal usarías tú para evaluar este ángulo en tu propio trabajo? "
        "Borrador armado sin asistente; lo afilo en el siguiente pase."
    )

    hashtags: list[str] = []
    if "agentic" in angle_lower or "agent" in angle_lower:
        hashtags.append("AgenticWorkflows")
    if (
        "machine learning" in angle_lower
        or "ml" in angle_lower
        or "neural" in angle_lower
    ):
        hashtags.append("MachineLearning")
    if not hashtags:
        hashtags = ["AppliedAI", "AppliedDecisionSystems"]
    return LinkedInPost(
        hook=hook,
        body_paragraphs=body_paragraphs,
        closing=closing,
        hashtags=hashtags,
    )


class OpenAILinkedInWriter:
    """Structured LinkedIn post — provider-agnostic via _structured_completion."""

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
        context: LinkedInPostInput,
    ) -> LinkedInPost | None:
        return await _structured_completion(
            self._client,
            model=self._model,
            system=LINKEDIN_SYSTEM_PROMPT,
            user=build_linkedin_user_prompt(context),
            response_cls=LinkedInPost,
            max_tokens=900,
            temperature=0.4,
        )


_writer: OpenAILinkedInWriter | None = None


def get_linkedin_writer() -> OpenAILinkedInWriter | None:
    global _writer
    if _writer is None and settings.openai_api_key:
        _writer = OpenAILinkedInWriter(
            api_key=settings.openai_api_key,
            model=settings.editorial_model,
            timeout_seconds=settings.linkedin_writer_timeout_seconds,
        )
        logger.info(
            "OpenAILinkedInWriter initialized (model=%s).",
            settings.editorial_model,
        )
    return _writer


async def build_linkedin_post(
    db: aiosqlite.Connection,
    plan_id: int,
) -> tuple[LinkedInPost, bool]:
    """Returns (post, llm_used). Falls back deterministically on any failure."""
    context = await _build_input(db, plan_id)
    writer = get_linkedin_writer()
    if writer is not None:
        try:
            generated = await writer.generate(context)
        except Exception as exc:  # belt-and-suspenders
            logger.warning("LinkedIn writer raised: %s", exc)
            generated = None
        if generated is not None:
            return generated, True
    return _fallback_post(context), False


async def build_linkedin_prompt_kit(
    db: aiosqlite.Connection,
    plan_id: int,
) -> LinkedInPromptKit:
    """Assemble a portable kit Carlos can paste into another LLM."""
    context = await _build_input(db, plan_id)
    system, user, one_line = build_linkedin_prompt_kit_text(context)
    return LinkedInPromptKit(
        plan_id=plan_id,
        system_prompt=system,
        user_prompt=user,
        one_line_paste_command=one_line,
    )
