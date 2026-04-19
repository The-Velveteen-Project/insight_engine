"""
Tests for Phase 6 editorial planning.

The deterministic planner is tested directly, and the OpenAI generation
layer is always mocked.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.schemas.discovery import SignalCandidate
from app.schemas.editorial import (
    DecisionBasis,
    DraftOutline,
    EditorialPlan,
    EditorialPlanRequest,
    EditorialPlanStatus,
    GeneratedEditorialDraft,
    PersistedEditorialPlan,
    RecommendedAction,
)
from app.services.editorial_planner import (
    EditorialPlanTransitionError,
    _choose_action,
    create_persisted_editorial_plan,
    get_persisted_editorial_plan,
    plan_editorial,
    transition_editorial_plan,
)
from app.services.generation import OpenAIEditorialGenerator


def _signal(
    *,
    source_type: str,
    source_id: str,
    title: str,
    summary: str,
    relevance_score: float,
) -> SignalCandidate:
    if source_type == "github":
        url = f"https://github.com/{source_id.split('::')[0]}"
    elif source_type == "arxiv":
        url = f"https://arxiv.org/abs/{source_id}"
    else:
        url = f"https://news.ycombinator.com/item?id={source_id}"

    return SignalCandidate(
        source_type=source_type,
        source_id=source_id,
        title=title,
        url=url,  # type: ignore[arg-type]
        summary=summary,
        raw_content="",
        relevance_score=relevance_score,
        relevance_note="test note",
    )


async def _insert_signal(
    db: aiosqlite.Connection,
    signal: SignalCandidate,
) -> int:
    cursor = await db.execute(
        """
        INSERT INTO signals (
            source_type, source_id, title, url, summary,
            raw_content, relevance_score, relevance_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal.source_type,
            signal.source_id,
            signal.title,
            str(signal.url),
            signal.summary,
            signal.raw_content,
            signal.relevance_score,
            signal.relevance_note,
        ),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _mock_plan(signal_ids: list[int]) -> EditorialPlan:
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


def test_plan_request_dedupes_signal_ids() -> None:
    payload = EditorialPlanRequest(signal_ids=[3, 3, 2, 3, 1])
    assert payload.signal_ids == [3, 2, 1]


def test_plan_request_limits_signal_count_to_three() -> None:
    with pytest.raises(ValueError):
        EditorialPlanRequest(signal_ids=[1, 2, 3, 4])


def test_editorial_plan_requires_human_review_true() -> None:
    with pytest.raises(ValueError):
        EditorialPlan(
            signal_ids=[1],
            recommended_action=RecommendedAction.NOTE,
            decision_basis=DecisionBasis(
                primary_signal_id=1,
                supporting_signal_ids=[],
                source_types=["arxiv"],
                matched_rule="strong_arxiv_note",
                confidence_factors=["base=0.35", "max_relevance=0.45"],
            ),
            why_it_matters="A technical explanation with enough substance.",
            angle="Explain the lesson clearly",
            draft_outline=DraftOutline(
                hook="Explain the signal clearly",
                points=["Point one", "Point two"],
                closing="Close with one implication",
            ),
            portfolio_value="Shows technical judgment and clarity.",
            confidence=0.61,
            llm_used=False,
            fallback_used=True,
            needs_human_review=False,
        )


def test_choose_action_prefers_mvp_for_mixed_signals() -> None:
    action = _choose_action(
        [
            SimpleNamespace(
                source_type="github",
                source_id="owner/repo::artifact::pyproject.toml",
                title="EcoAgent artifact signal",
                summary="FastAPI workflow for agent system",
                relevance_note="",
                relevance_score=0.82,
            ),
            SimpleNamespace(
                source_type="arxiv",
                source_id="2401.10001",
                title="Agentic evaluation paper",
                summary="Research on agent benchmark and evaluation pipeline",
                relevance_note="",
                relevance_score=0.76,
            ),
        ]
    )
    assert action == RecommendedAction.MVP


def test_choose_action_archives_weak_signal() -> None:
    action = _choose_action(
        [
            SimpleNamespace(
                source_type="hackernews",
                source_id="weak-signal",
                title="Minor trend mention",
                summary="A small signal with little technical depth.",
                relevance_note="",
                relevance_score=0.24,
            )
        ]
    )
    assert action == RecommendedAction.ARCHIVE


def test_choose_action_returns_note_for_useful_but_immature_signal() -> None:
    action = _choose_action(
        [
            SimpleNamespace(
                source_type="arxiv",
                source_id="2401.30001",
                title="Bayesian climate risk benchmark",
                summary="Research study on climate risk modeling and evaluation.",
                relevance_note="research climate risk",
                relevance_score=0.58,
            )
        ]
    )
    assert action == RecommendedAction.NOTE


def test_choose_action_returns_post_for_strong_clear_public_angle() -> None:
    action = _choose_action(
        [
            SimpleNamespace(
                source_type="hackernews",
                source_id="post-signal",
                title="Workflow insight signal update",
                summary="A clear workflow lesson and public insight for AI systems.",
                relevance_note="signal workflow insight lesson",
                relevance_score=0.78,
            )
        ]
    )
    assert action == RecommendedAction.POST


def test_choose_action_archives_incoherent_multi_signal_set() -> None:
    action = _choose_action(
        [
            SimpleNamespace(
                source_type="arxiv",
                source_id="2401.40001",
                title="Bayesian climate model",
                summary="Research on climate modeling and risk.",
                relevance_note="climate research",
                relevance_score=0.56,
            ),
            SimpleNamespace(
                source_type="github",
                source_id="owner/repo::artifact::dockerfile",
                title="Frontend asset pipeline",
                summary="Build system for image assets and deployment.",
                relevance_note="deployment assets",
                relevance_score=0.44,
            ),
        ]
    )
    assert action == RecommendedAction.ARCHIVE


def test_choose_action_prefers_note_when_signals_are_incoherent_but_useful() -> None:
    action = _choose_action(
        [
            SimpleNamespace(
                source_type="arxiv",
                source_id="2401.50001",
                title="Health modeling study",
                summary="Research on health risk modeling and evaluation.",
                relevance_note="health research",
                relevance_score=0.68,
            ),
            SimpleNamespace(
                source_type="github",
                source_id="owner/repo::overview",
                title="Tooling repository update",
                summary="Build pipeline and API cleanup for internal tooling.",
                relevance_note="api tooling",
                relevance_score=0.62,
            ),
        ]
    )
    assert action == RecommendedAction.NOTE


async def test_plan_editorial_uses_llm_draft_when_available(
    db: aiosqlite.Connection,
) -> None:
    signal_id = await _insert_signal(
        db,
        _signal(
            source_type="arxiv",
            source_id="2401.00001",
            title="Bayesian climate risk paper",
            summary="Research note on climate risk modeling and evaluation.",
            relevance_score=0.74,
        ),
    )

    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GeneratedEditorialDraft(
                why_it_matters=(
                    "This signal matters because it sharpens the research thesis."
                ),
                angle="Technical angle on climate risk modeling",
                draft_outline=DraftOutline(
                    hook="Start from the modeling question.",
                    points=["Method and constraint", "Implication for next build"],
                    closing="Close with the next technical implication.",
                ),
                portfolio_value="Turns research into a reusable technical note.",
            )
        )
    )

    with patch(
        "app.services.editorial_planner.get_editorial_generator",
        return_value=generator,
    ):
        plan = await plan_editorial(db, [signal_id])

    assert plan.recommended_action == RecommendedAction.NOTE
    assert plan.signal_ids == [signal_id]
    assert plan.angle == "Technical angle on climate risk modeling"
    assert plan.llm_used is True
    assert plan.fallback_used is False
    assert plan.needs_human_review is True


async def test_plan_editorial_falls_back_when_generator_missing(
    db: aiosqlite.Connection,
) -> None:
    signal_id = await _insert_signal(
        db,
        _signal(
            source_type="hackernews",
            source_id="12345",
            title="Useful workflow signal update",
            summary=(
                "Applied AI workflow insight and public lesson for technical teams."
            ),
            relevance_score=0.78,
        ),
    )

    with patch(
        "app.services.editorial_planner.get_editorial_generator",
        return_value=None,
    ):
        plan = await plan_editorial(db, [signal_id])

    assert plan.recommended_action == RecommendedAction.POST
    assert len(plan.draft_outline.points) >= 2
    assert plan.decision_basis.matched_rule == "strong_public_angle_post"
    assert plan.llm_used is False
    assert plan.fallback_used is True
    assert any(
        "base=0.30" in factor for factor in plan.decision_basis.confidence_factors
    )
    assert plan.confidence == pytest.approx(0.78)
    assert plan.needs_human_review is True


async def test_plan_editorial_raises_when_signals_missing(
    db: aiosqlite.Connection,
) -> None:
    with pytest.raises(LookupError):
        await plan_editorial(db, [9999])


async def test_plan_editorial_archive_basis_and_confidence_for_incoherent_set(
    db: aiosqlite.Connection,
) -> None:
    first_signal_id = await _insert_signal(
        db,
        _signal(
            source_type="arxiv",
            source_id="2401.61001",
            title="Bayesian climate model",
            summary="Research on climate modeling and risk.",
            relevance_score=0.56,
        ),
    )
    second_signal_id = await _insert_signal(
        db,
        _signal(
            source_type="github",
            source_id="owner/repo::artifact::dockerfile",
            title="Frontend asset pipeline",
            summary="Build system for image assets and deployment.",
            relevance_score=0.44,
        ),
    )

    with patch(
        "app.services.editorial_planner.get_editorial_generator",
        return_value=None,
    ):
        plan = await plan_editorial(db, [first_signal_id, second_signal_id])

    assert plan.recommended_action == RecommendedAction.ARCHIVE
    assert plan.decision_basis.matched_rule == "incoherent_multi_signal_archive"
    assert "archive_penalty=-0.20" in plan.decision_basis.confidence_factors
    assert "incoherent_mix_penalty=-0.12" in plan.decision_basis.confidence_factors
    assert "cross_source_bonus=0.10" not in plan.decision_basis.confidence_factors
    assert plan.confidence == pytest.approx(0.48)


async def test_plan_editorial_note_basis_for_useful_but_immature_signal(
    db: aiosqlite.Connection,
) -> None:
    signal_id = await _insert_signal(
        db,
        _signal(
            source_type="arxiv",
            source_id="2401.62001",
            title="Bayesian climate risk benchmark",
            summary="Research study on climate risk modeling and evaluation.",
            relevance_score=0.58,
        ),
    )

    with patch(
        "app.services.editorial_planner.get_editorial_generator",
        return_value=None,
    ):
        plan = await plan_editorial(db, [signal_id])

    assert plan.recommended_action == RecommendedAction.NOTE
    assert plan.decision_basis.matched_rule == "useful_but_immature_note"
    assert plan.confidence == pytest.approx(0.78)


async def test_plan_editorial_mvp_basis_for_strong_buildable_convergence(
    db: aiosqlite.Connection,
) -> None:
    github_signal_id = await _insert_signal(
        db,
        _signal(
            source_type="github",
            source_id="owner/repo::artifact::pyproject.toml",
            title="EcoAgent workflow system artifact",
            summary="FastAPI tool for agent evaluation workflow and API pipeline.",
            relevance_score=0.82,
        ),
    )
    external_signal_id = await _insert_signal(
        db,
        _signal(
            source_type="arxiv",
            source_id="2401.63001",
            title="Agent workflow evaluation paper",
            summary="Research on agent benchmark, workflow design, and evaluation.",
            relevance_score=0.76,
        ),
    )

    with patch(
        "app.services.editorial_planner.get_editorial_generator",
        return_value=None,
    ):
        plan = await plan_editorial(db, [github_signal_id, external_signal_id])

    assert plan.recommended_action == RecommendedAction.MVP
    assert plan.decision_basis.matched_rule == "mixed_github_external_build_signal"
    assert "cross_source_bonus=0.10" in plan.decision_basis.confidence_factors
    assert "mvp_bonus=0.07" in plan.decision_basis.confidence_factors
    assert plan.confidence == pytest.approx(0.95)


async def test_plan_editorial_raises_when_some_signals_missing(
    db: aiosqlite.Connection,
) -> None:
    signal_id = await _insert_signal(
        db,
        _signal(
            source_type="arxiv",
            source_id="2401.20001",
            title="One valid signal",
            summary="Research signal",
            relevance_score=0.61,
        ),
    )
    with pytest.raises(LookupError):
        await plan_editorial(db, [signal_id, 9999])


async def test_generation_service_returns_structured_output() -> None:
    generator = OpenAIEditorialGenerator(api_key="test-key", model="gpt-4.1-mini")
    fake_response = SimpleNamespace(
        output_parsed=GeneratedEditorialDraft(
            why_it_matters="The signal connects public research to concrete execution.",
            angle="Bridge the research signal into a sober plan",
            draft_outline=DraftOutline(
                hook="Signal setup",
                points=["Lesson", "Constraint"],
                closing="Next step now",
            ),
            portfolio_value="Creates a reusable portfolio artifact.",
        )
    )
    generator._client = SimpleNamespace(  # type: ignore[assignment]
        responses=SimpleNamespace(parse=AsyncMock(return_value=fake_response))
    )

    result = await generator.generate(
        SimpleNamespace(  # type: ignore[arg-type]
            recommended_action=RecommendedAction.NOTE,
            confidence=0.72,
            rationale_hint="The signal supports a technical note.",
            angle_hint="Explain the lesson and the implication.",
            signals=[
                SimpleNamespace(
                    id=1,
                    source_type="arxiv",
                    source_id="2401.00001",
                    title="Signal",
                    summary="Summary",
                    relevance_score=0.72,
                    relevance_note="note",
                )
            ],
        )
    )

    assert result is not None
    assert result.portfolio_value == "Creates a reusable portfolio artifact."


async def test_create_persisted_editorial_plan_stores_draft(
    db: aiosqlite.Connection,
) -> None:
    signal_id = await _insert_signal(
        db,
        _signal(
            source_type="arxiv",
            source_id="2401.70001",
            title="Persisted plan signal",
            summary="Research signal for persistence.",
            relevance_score=0.64,
        ),
    )
    mock_plan = _mock_plan([signal_id])

    with patch(
        "app.services.editorial_planner.plan_editorial",
        new=AsyncMock(return_value=mock_plan),
    ):
        stored = await create_persisted_editorial_plan(db, [signal_id])

    assert stored.status == EditorialPlanStatus.DRAFT
    assert stored.plan_id > 0
    assert stored.proposal.signal_ids == [signal_id]
    assert stored.reviewed_at is None


async def test_transition_editorial_plan_approve_then_save(
    db: aiosqlite.Connection,
) -> None:
    signal_id = await _insert_signal(
        db,
        _signal(
            source_type="arxiv",
            source_id="2401.70002",
            title="Workflow signal",
            summary="Signal for approval workflow.",
            relevance_score=0.66,
        ),
    )
    mock_plan = _mock_plan([signal_id])

    with patch(
        "app.services.editorial_planner.plan_editorial",
        new=AsyncMock(return_value=mock_plan),
    ):
        stored = await create_persisted_editorial_plan(db, [signal_id])

    approved = await transition_editorial_plan(
        db,
        stored.plan_id,
        EditorialPlanStatus.APPROVED,
    )
    assert approved.status == EditorialPlanStatus.APPROVED
    assert approved.reviewed_at is not None

    saved = await transition_editorial_plan(
        db,
        stored.plan_id,
        EditorialPlanStatus.SAVED,
    )
    assert saved.status == EditorialPlanStatus.SAVED
    assert saved.reviewed_at is not None


async def test_transition_editorial_plan_rejects_invalid_transition(
    db: aiosqlite.Connection,
) -> None:
    signal_id = await _insert_signal(
        db,
        _signal(
            source_type="arxiv",
            source_id="2401.70003",
            title="State transition signal",
            summary="Signal for invalid transition case.",
            relevance_score=0.66,
        ),
    )
    mock_plan = _mock_plan([signal_id])

    with patch(
        "app.services.editorial_planner.plan_editorial",
        new=AsyncMock(return_value=mock_plan),
    ):
        stored = await create_persisted_editorial_plan(db, [signal_id])

    await transition_editorial_plan(
        db,
        stored.plan_id,
        EditorialPlanStatus.DISCARDED,
    )

    with pytest.raises(
        EditorialPlanTransitionError,
        match="Invalid editorial plan transition",
    ):
        await transition_editorial_plan(
            db,
            stored.plan_id,
            EditorialPlanStatus.APPROVED,
        )


async def test_get_persisted_editorial_plan_raises_for_missing(
    db: aiosqlite.Connection,
) -> None:
    with pytest.raises(LookupError):
        await get_persisted_editorial_plan(db, 9999)


async def test_editorial_route_returns_200(client) -> None:
    mock_plan = PersistedEditorialPlan(
        plan_id=11,
        status=EditorialPlanStatus.DRAFT,
        proposal=_mock_plan([1, 2]),
        created_at=datetime.fromisoformat("2026-04-19T12:00:00"),
        updated_at=datetime.fromisoformat("2026-04-19T12:00:00"),
        reviewed_at=None,
    )

    with patch(
        "app.api.routes.editorial.editorial_planner.create_persisted_editorial_plan",
        new=AsyncMock(return_value=mock_plan),
    ):
        response = await client.post(
            "/api/v1/editorial/plan",
            json={"signal_ids": [1, 2]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == 11
    assert body["status"] == "draft"
    assert body["proposal"]["recommended_action"] == "note"
    assert body["proposal"]["signal_ids"] == [1, 2]
    assert body["proposal"]["llm_used"] is True
    assert body["proposal"]["needs_human_review"] is True


async def test_editorial_route_returns_404_for_missing_signals(client) -> None:
    with patch(
        "app.api.routes.editorial.editorial_planner.create_persisted_editorial_plan",
        new=AsyncMock(side_effect=LookupError("No persisted signals were found.")),
    ):
        response = await client.post(
            "/api/v1/editorial/plan",
            json={"signal_ids": [999]},
        )

    assert response.status_code == 404


async def test_editorial_route_rejects_more_than_three_signals(client) -> None:
    response = await client.post(
        "/api/v1/editorial/plan",
        json={"signal_ids": [1, 2, 3, 4]},
    )

    assert response.status_code == 422


async def test_get_editorial_plan_route_returns_200(client) -> None:
    mock_plan = PersistedEditorialPlan(
        plan_id=12,
        status=EditorialPlanStatus.APPROVED,
        proposal=_mock_plan([3]),
        created_at=datetime.fromisoformat("2026-04-19T12:00:00"),
        updated_at=datetime.fromisoformat("2026-04-19T12:05:00"),
        reviewed_at=datetime.fromisoformat("2026-04-19T12:04:00"),
    )

    with patch(
        "app.api.routes.editorial.editorial_planner.get_persisted_editorial_plan",
        new=AsyncMock(return_value=mock_plan),
    ):
        response = await client.get("/api/v1/editorial/plans/12")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_get_editorial_plan_route_returns_404(client) -> None:
    with patch(
        "app.api.routes.editorial.editorial_planner.get_persisted_editorial_plan",
        new=AsyncMock(side_effect=LookupError("Editorial plan not found: 404.")),
    ):
        response = await client.get("/api/v1/editorial/plans/404")

    assert response.status_code == 404


async def test_approve_editorial_plan_route_returns_200(client) -> None:
    mock_plan = PersistedEditorialPlan(
        plan_id=13,
        status=EditorialPlanStatus.APPROVED,
        proposal=_mock_plan([4]),
        created_at=datetime.fromisoformat("2026-04-19T12:00:00"),
        updated_at=datetime.fromisoformat("2026-04-19T12:06:00"),
        reviewed_at=datetime.fromisoformat("2026-04-19T12:06:00"),
    )

    with patch(
        "app.api.routes.editorial._transition_plan",
        new=AsyncMock(return_value=mock_plan),
    ):
        response = await client.post("/api/v1/editorial/plans/13/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_save_editorial_plan_route_returns_200(client) -> None:
    mock_plan = PersistedEditorialPlan(
        plan_id=14,
        status=EditorialPlanStatus.SAVED,
        proposal=_mock_plan([5]),
        created_at=datetime.fromisoformat("2026-04-19T12:00:00"),
        updated_at=datetime.fromisoformat("2026-04-19T12:08:00"),
        reviewed_at=datetime.fromisoformat("2026-04-19T12:06:00"),
    )

    with patch(
        "app.api.routes.editorial._transition_plan",
        new=AsyncMock(return_value=mock_plan),
    ):
        response = await client.post("/api/v1/editorial/plans/14/save")

    assert response.status_code == 200
    assert response.json()["status"] == "saved"


async def test_discard_editorial_plan_route_returns_409_on_invalid_transition(
    client,
) -> None:
    with patch(
        "app.api.routes.editorial.editorial_planner.transition_editorial_plan",
        new=AsyncMock(
            side_effect=EditorialPlanTransitionError(
                "Invalid editorial plan transition: saved -> discarded."
            )
        ),
    ):
        response = await client.post("/api/v1/editorial/plans/15/discard")

    assert response.status_code == 409
