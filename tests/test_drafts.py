"""
Tests for Phase 8 editorial draft generation and persistence.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.db.queries import insert_editorial_plan
from app.schemas.drafts import (
    EditorialDraft,
    EditorialDraftContent,
    EditorialDraftStatus,
    PersistedEditorialDraft,
)
from app.schemas.editorial import (
    DecisionBasis,
    DraftOutline,
    EditorialPlan,
    EditorialPlanStatus,
    RecommendedAction,
)
from app.services.draft_generator import (
    DraftGenerationStateError,
    EditorialDraftConflictError,
    EditorialDraftTransitionError,
    create_persisted_editorial_draft,
    discard_persisted_editorial_draft,
    get_persisted_editorial_draft,
)
from app.services.generation import OpenAIDraftGenerator


def _plan(signal_ids: list[int]) -> EditorialPlan:
    primary_signal_id = signal_ids[0]
    supporting_signal_ids = signal_ids[1:]
    return EditorialPlan(
        signal_ids=signal_ids,
        recommended_action=RecommendedAction.NOTE,
        decision_basis=DecisionBasis(
            primary_signal_id=primary_signal_id,
            supporting_signal_ids=supporting_signal_ids,
            source_types=["arxiv"],
            matched_rule="default_note",
            confidence_factors=["base=0.30", "max_relevance=0.40"],
        ),
        why_it_matters="The signal is strong enough to justify a sober technical note.",
        angle="Explain the lesson and the constraint",
        draft_outline=DraftOutline(
            hook="State the signal",
            points=["Extract the technical lesson", "Clarify the main constraint"],
            closing="Close with the next implication",
        ),
        portfolio_value="Turns a signal into a reusable portfolio artifact.",
        confidence=0.68,
        llm_used=True,
        fallback_used=False,
        needs_human_review=True,
    )


def _draft(plan_id: int, signal_ids: list[int]) -> EditorialDraft:
    return EditorialDraft(
        plan_id=plan_id,
        signal_ids=signal_ids,
        recommended_action=RecommendedAction.NOTE,
        source_angle="Explain the lesson and the constraint",
        llm_used=True,
        fallback_used=False,
        content=EditorialDraftContent(
            working_title="A sober working title for the approved plan",
            post_body=(
                "This is a structured draft body grounded in the approved plan. "
                "It stays technical, specific, and concise without drifting into hype."
            ),
            short_version=(
                "A concise version of the draft grounded in the approved plan."
            ),
            cta="Worth pursuing only if the next pass adds concrete evidence.",
            tone_notes=[
                "Keep it technical and specific.",
                "Avoid hype or generic commentary.",
            ],
        ),
    )


async def test_create_persisted_editorial_draft_stores_draft(
    db: aiosqlite.Connection,
) -> None:
    plan_id = await insert_editorial_plan(
        db,
        _plan([1, 2]),
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.draft_generator.get_draft_generator",
        return_value=None,
    ):
        stored = await create_persisted_editorial_draft(db, plan_id)

    assert stored.plan_id == plan_id
    assert stored.status == EditorialDraftStatus.DRAFT
    assert stored.draft.plan_id == plan_id
    assert stored.draft.fallback_used is True
    assert stored.draft.llm_used is False
    assert stored.draft.signal_ids == [1, 2]
    assert stored.draft.recommended_action == RecommendedAction.NOTE


async def test_create_persisted_editorial_draft_requires_approved_plan(
    db: aiosqlite.Connection,
) -> None:
    plan_id = await insert_editorial_plan(
        db,
        _plan([3]),
        status=EditorialPlanStatus.DRAFT,
    )

    with pytest.raises(
        DraftGenerationStateError,
        match="requires an approved plan",
    ):
        await create_persisted_editorial_draft(db, plan_id)


async def test_create_persisted_editorial_draft_rejects_existing_draft(
    db: aiosqlite.Connection,
) -> None:
    plan_id = await insert_editorial_plan(
        db,
        _plan([4]),
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.draft_generator.get_draft_generator",
        return_value=None,
    ):
        first = await create_persisted_editorial_draft(db, plan_id)

    with pytest.raises(EditorialDraftConflictError) as exc_info:
        await create_persisted_editorial_draft(db, plan_id)

    assert exc_info.value.draft_id == first.draft_id
    assert exc_info.value.plan_id == plan_id


async def test_discard_persisted_editorial_draft(
    db: aiosqlite.Connection,
) -> None:
    plan_id = await insert_editorial_plan(
        db,
        _plan([5]),
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.draft_generator.get_draft_generator",
        return_value=None,
    ):
        stored = await create_persisted_editorial_draft(db, plan_id)

    discarded = await discard_persisted_editorial_draft(db, stored.draft_id)
    assert discarded.status == EditorialDraftStatus.DISCARDED


async def test_discard_persisted_editorial_draft_rejects_second_discard(
    db: aiosqlite.Connection,
) -> None:
    plan_id = await insert_editorial_plan(
        db,
        _plan([6]),
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.draft_generator.get_draft_generator",
        return_value=None,
    ):
        stored = await create_persisted_editorial_draft(db, plan_id)

    await discard_persisted_editorial_draft(db, stored.draft_id)
    with pytest.raises(
        EditorialDraftTransitionError,
        match="Invalid editorial draft transition",
    ):
        await discard_persisted_editorial_draft(db, stored.draft_id)


async def test_get_persisted_editorial_draft_raises_for_missing(
    db: aiosqlite.Connection,
) -> None:
    with pytest.raises(LookupError):
        await get_persisted_editorial_draft(db, 9999)


async def test_draft_generation_service_returns_structured_output() -> None:
    generator = OpenAIDraftGenerator(api_key="test-key", model="gpt-4.1-mini")
    fake_parsed = EditorialDraftContent(
        working_title="A sober working title for the approved plan",
        post_body=(
            "This is a structured draft body grounded in the approved plan. "
            "It stays technical, specific, and concise without drifting into hype."
        ),
        short_version=(
            "A concise version of the draft grounded in the approved plan."
        ),
        cta="Worth pursuing only if the next pass adds concrete evidence.",
        tone_notes=[
            "Keep it technical and specific.",
            "Avoid hype or generic commentary.",
        ],
    )
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=fake_parsed))]
    )
    generator._client = SimpleNamespace(  # type: ignore[assignment]
        beta=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    parse=AsyncMock(return_value=fake_response)
                )
            )
        )
    )

    result = await generator.generate(
        SimpleNamespace(  # type: ignore[arg-type]
            plan_id=1,
            signal_ids=[1, 2],
            recommended_action=RecommendedAction.NOTE,
            source_angle="Explain the lesson and the constraint",
            why_it_matters="This matters because it sharpens the technical thesis.",
            draft_hook="State the signal",
            draft_points=["Extract the lesson", "Clarify the constraint"],
            draft_closing="Close with the next implication",
            portfolio_value="Turns the plan into a usable artifact.",
        )
    )

    assert result is not None
    assert result.working_title == "A sober working title for the approved plan"


async def test_create_editorial_draft_route_returns_200(client) -> None:
    mock_draft = PersistedEditorialDraft(
        draft_id=21,
        plan_id=11,
        status=EditorialDraftStatus.DRAFT,
        draft=_draft(11, [1, 2]),
        created_at=datetime.fromisoformat("2026-04-19T12:00:00"),
        updated_at=datetime.fromisoformat("2026-04-19T12:00:00"),
    )

    with patch(
        "app.api.routes.editorial.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(return_value=mock_draft),
    ):
        response = await client.post("/api/v1/editorial/plans/11/draft")

    assert response.status_code == 200
    body = response.json()
    assert body["draft_id"] == 21
    assert body["plan_id"] == 11
    assert body["status"] == "draft"
    assert body["draft"]["plan_id"] == 11
    assert body["draft"]["llm_used"] is True


async def test_create_editorial_draft_route_returns_409_for_existing_draft(
    client,
) -> None:
    with patch(
        "app.api.routes.editorial.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(
            side_effect=EditorialDraftConflictError(plan_id=11, draft_id=21)
        ),
    ):
        response = await client.post("/api/v1/editorial/plans/11/draft")

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["draft_id"] == 21
    assert body["detail"]["plan_id"] == 11


async def test_create_editorial_draft_route_returns_409_for_invalid_plan_state(
    client,
) -> None:
    with patch(
        "app.api.routes.editorial.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(
            side_effect=DraftGenerationStateError(
                "Editorial draft generation requires an approved plan. "
                "Current status: draft."
            )
        ),
    ):
        response = await client.post("/api/v1/editorial/plans/11/draft")

    assert response.status_code == 409
    assert "requires an approved plan" in response.json()["detail"]


async def test_get_editorial_draft_route_returns_200(client) -> None:
    mock_draft = PersistedEditorialDraft(
        draft_id=22,
        plan_id=12,
        status=EditorialDraftStatus.DRAFT,
        draft=_draft(12, [3]),
        created_at=datetime.fromisoformat("2026-04-19T12:00:00"),
        updated_at=datetime.fromisoformat("2026-04-19T12:01:00"),
    )

    with patch(
        "app.api.routes.editorial.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(return_value=mock_draft),
    ):
        response = await client.get("/api/v1/editorial/drafts/22")

    assert response.status_code == 200
    assert response.json()["draft_id"] == 22


async def test_get_editorial_draft_route_returns_404(client) -> None:
    with patch(
        "app.api.routes.editorial.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(side_effect=LookupError("Editorial draft not found: 404.")),
    ):
        response = await client.get("/api/v1/editorial/drafts/404")

    assert response.status_code == 404


async def test_discard_editorial_draft_route_returns_200(client) -> None:
    mock_draft = PersistedEditorialDraft(
        draft_id=23,
        plan_id=13,
        status=EditorialDraftStatus.DISCARDED,
        draft=_draft(13, [4]),
        created_at=datetime.fromisoformat("2026-04-19T12:00:00"),
        updated_at=datetime.fromisoformat("2026-04-19T12:02:00"),
    )

    with patch(
        "app.api.routes.editorial.draft_generator.discard_persisted_editorial_draft",
        new=AsyncMock(return_value=mock_draft),
    ):
        response = await client.post("/api/v1/editorial/drafts/23/discard")

    assert response.status_code == 200
    assert response.json()["status"] == "discarded"


async def test_discard_editorial_draft_route_returns_409(client) -> None:
    with patch(
        "app.api.routes.editorial.draft_generator.discard_persisted_editorial_draft",
        new=AsyncMock(
            side_effect=EditorialDraftTransitionError(
                "Invalid editorial draft transition: discarded -> discarded."
            )
        ),
    ):
        response = await client.post("/api/v1/editorial/drafts/23/discard")

    assert response.status_code == 409
