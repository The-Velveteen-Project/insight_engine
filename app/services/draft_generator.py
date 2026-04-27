"""
Draft generator for Phase 8.

Generates structured drafts only from persisted, approved editorial plans.
"""

from __future__ import annotations

import logging

import aiosqlite

from app.db.queries import (
    get_editorial_draft_by_id,
    get_editorial_draft_by_plan_id,
    insert_editorial_draft,
    update_editorial_draft_status,
)
from app.schemas.drafts import (
    DraftGenerationInput,
    EditorialDraft,
    EditorialDraftContent,
    EditorialDraftStatus,
    PersistedEditorialDraft,
)
from app.schemas.editorial import EditorialPlanStatus, PersistedEditorialPlan
from app.services.editorial_planner import get_persisted_editorial_plan
from app.services.generation import get_draft_generator

logger = logging.getLogger(__name__)

# Phase 8 draft state machine:
# - draft -> discarded
# - discarded -> terminal
_ALLOWED_DRAFT_STATUS_TRANSITIONS: dict[
    EditorialDraftStatus, set[EditorialDraftStatus]
] = {
    EditorialDraftStatus.DRAFT: {EditorialDraftStatus.DISCARDED},
    EditorialDraftStatus.DISCARDED: set(),
}


class DraftGenerationStateError(Exception):
    """Raised when the source editorial plan is not in a valid state."""


class EditorialDraftConflictError(Exception):
    """Raised when a draft already exists for the plan."""

    def __init__(self, *, plan_id: int, draft_id: int) -> None:
        self.plan_id = plan_id
        self.draft_id = draft_id
        super().__init__(
            f"An editorial draft already exists for plan {plan_id}: draft {draft_id}."
        )


class EditorialDraftTransitionError(Exception):
    """Raised when an editorial draft state change is invalid."""


def _to_persisted_editorial_draft(row: aiosqlite.Row) -> PersistedEditorialDraft:
    draft = EditorialDraft.model_validate_json(str(row["draft_json"]))
    return PersistedEditorialDraft(
        draft_id=int(row["id"]),
        plan_id=int(row["plan_id"]),
        status=EditorialDraftStatus(str(row["status"])),
        draft=draft,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _build_generation_input(plan: PersistedEditorialPlan) -> DraftGenerationInput:
    proposal = plan.proposal
    return DraftGenerationInput(
        plan_id=plan.plan_id,
        signal_ids=proposal.signal_ids,
        recommended_action=proposal.recommended_action,
        source_angle=proposal.angle,
        why_it_matters=proposal.why_it_matters,
        draft_hook=proposal.draft_outline.hook,
        draft_points=proposal.draft_outline.points,
        draft_closing=proposal.draft_outline.closing,
        portfolio_value=proposal.portfolio_value,
    )


def _fallback_content(plan: PersistedEditorialPlan) -> EditorialDraftContent:
    proposal = plan.proposal
    title = proposal.angle.rstrip(".")
    working_title = f"{proposal.recommended_action.value.title()}: {title}"
    post_body = (
        f"{proposal.why_it_matters}\n\n"
        f"Angle: {proposal.angle}.\n"
        f"{proposal.draft_outline.hook}\n"
        f"- {proposal.draft_outline.points[0]}\n"
        f"- {proposal.draft_outline.points[1]}\n"
        f"{proposal.draft_outline.closing}\n\n"
        f"Portfolio value: {proposal.portfolio_value}"
    )
    short_version = (
        f"{proposal.angle}. {proposal.why_it_matters} "
        f"{proposal.draft_outline.closing}"
    )
    cta = "Worth developing further only if the next iteration stays specific."
    tone_notes = [
        "Keep the draft technical and specific.",
        "Avoid hype, sweeping claims, or generic inspiration language.",
    ]
    return EditorialDraftContent(
        working_title=working_title[:160],
        post_body=post_body[:2200],
        short_version=short_version[:420],
        cta=cta,
        tone_notes=tone_notes,
    )


def _build_editorial_draft(
    plan: PersistedEditorialPlan,
    content: EditorialDraftContent,
    *,
    llm_used: bool,
) -> EditorialDraft:
    proposal = plan.proposal
    return EditorialDraft(
        plan_id=plan.plan_id,
        signal_ids=proposal.signal_ids,
        recommended_action=proposal.recommended_action,
        source_angle=proposal.angle,
        llm_used=llm_used,
        fallback_used=not llm_used,
        content=content,
    )


async def create_persisted_editorial_draft(
    db: aiosqlite.Connection,
    plan_id: int,
    *,
    goal_id: int | None = None,
) -> PersistedEditorialDraft:
    plan = await get_persisted_editorial_plan(db, plan_id)
    if plan.status != EditorialPlanStatus.APPROVED:
        raise DraftGenerationStateError(
            "Editorial draft generation requires an approved plan. "
            f"Current status: {plan.status.value}."
        )

    existing = await get_editorial_draft_by_plan_id(db, plan_id)
    if existing is not None:
        raise EditorialDraftConflictError(
            plan_id=plan_id,
            draft_id=int(existing["id"]),
        )

    generation_input = _build_generation_input(plan)
    generator = get_draft_generator()
    generated = await generator.generate(generation_input) if generator else None
    llm_used = generated is not None
    if generated is None:
        logger.info("Draft generator using deterministic fallback content.")
        generated = _fallback_content(plan)

    draft = _build_editorial_draft(plan, generated, llm_used=llm_used)
    draft_id = await insert_editorial_draft(db, draft, goal_id=goal_id)
    row = await get_editorial_draft_by_id(db, draft_id)
    if row is None:
        raise LookupError(f"Persisted editorial draft was not found: {draft_id}.")
    return _to_persisted_editorial_draft(row)


async def get_persisted_editorial_draft(
    db: aiosqlite.Connection,
    draft_id: int,
) -> PersistedEditorialDraft:
    row = await get_editorial_draft_by_id(db, draft_id)
    if row is None:
        raise LookupError(f"Editorial draft not found: {draft_id}.")
    return _to_persisted_editorial_draft(row)


async def discard_persisted_editorial_draft(
    db: aiosqlite.Connection,
    draft_id: int,
) -> PersistedEditorialDraft:
    current = await get_persisted_editorial_draft(db, draft_id)
    if EditorialDraftStatus.DISCARDED not in _ALLOWED_DRAFT_STATUS_TRANSITIONS[
        current.status
    ]:
        raise EditorialDraftTransitionError(
            "Invalid editorial draft transition: "
            f"{current.status.value} -> discarded."
        )
    await update_editorial_draft_status(db, draft_id, EditorialDraftStatus.DISCARDED)
    return await get_persisted_editorial_draft(db, draft_id)
