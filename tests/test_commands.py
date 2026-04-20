"""
Tests for Telegram command parsing and orchestration.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import aiosqlite

from app.db.queries import insert_signal
from app.schemas.commands import (
    CommandName,
    MvpIdeaSuggestion,
    SignalSuggestion,
    WeeklySummary,
)
from app.schemas.discovery import SignalCandidate
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
    PersistedEditorialPlan,
    RecommendedAction,
)
from app.schemas.github import GitHubInsightCandidate
from app.services.draft_generator import (
    DraftGenerationStateError,
    EditorialDraftConflictError,
)
from app.services.editorial_planner import EditorialPlanTransitionError
from app.services.telegram_orchestrator import (
    build_mvp_idea,
    build_weekly_summary,
    handle_command,
    parse_command,
)


def _plan(signal_ids: list[int], action: RecommendedAction) -> EditorialPlan:
    return EditorialPlan(
        signal_ids=signal_ids,
        recommended_action=action,
        decision_basis=DecisionBasis(
            primary_signal_id=signal_ids[0],
            supporting_signal_ids=signal_ids[1:],
            source_types=["arxiv"],
            matched_rule="default_note",
            confidence_factors=["base=0.30", "max_relevance=0.40"],
        ),
        why_it_matters=(
            "This signal matters because it creates a concrete technical angle."
        ),
        angle="Use the signal to frame one concrete technical angle",
        draft_outline=DraftOutline(
            hook="State the signal",
            points=["Extract the lesson", "Clarify the next implication"],
            closing="Close with the next step",
        ),
        portfolio_value="Keeps the portfolio narrative grounded in applied work.",
        confidence=0.72,
        llm_used=False,
        fallback_used=True,
        needs_human_review=True,
    )


def _persisted_plan(
    plan_id: int,
    *,
    signal_ids: list[int],
    action: RecommendedAction = RecommendedAction.NOTE,
    status: EditorialPlanStatus = EditorialPlanStatus.DRAFT,
) -> PersistedEditorialPlan:
    timestamp = datetime.fromisoformat("2026-04-19T12:00:00")
    return PersistedEditorialPlan(
        plan_id=plan_id,
        status=status,
        proposal=_plan(signal_ids, action),
        created_at=timestamp,
        updated_at=timestamp,
        reviewed_at=timestamp if status != EditorialPlanStatus.DRAFT else None,
    )


def _draft(plan_id: int, signal_ids: list[int]) -> EditorialDraft:
    return EditorialDraft(
        plan_id=plan_id,
        signal_ids=signal_ids,
        recommended_action=RecommendedAction.NOTE,
        source_angle="Explain the lesson and the constraint",
        llm_used=False,
        fallback_used=True,
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


def _persisted_draft(draft_id: int, *, plan_id: int) -> PersistedEditorialDraft:
    timestamp = datetime.fromisoformat("2026-04-19T13:00:00")
    return PersistedEditorialDraft(
        draft_id=draft_id,
        plan_id=plan_id,
        status=EditorialDraftStatus.DRAFT,
        draft=_draft(plan_id, [1]),
        created_at=timestamp,
        updated_at=timestamp,
    )


def _signal_candidate(source_id: str, title: str) -> SignalCandidate:
    return SignalCandidate(
        source_type="arxiv",
        source_id=source_id,
        title=title,
        url=f"https://arxiv.org/abs/{source_id}",  # type: ignore[arg-type]
        summary="A relevant paper summary.",
        raw_content="",
        relevance_score=0.77,
        relevance_note="relevant research signal",
    )


def _github_candidate(source_id: str, title: str) -> GitHubInsightCandidate:
    return GitHubInsightCandidate(
        repo_full_name="The-Velveteen-Project/EcoAgent",
        insight_type="overview",
        source_id=source_id,
        title=title,
        url="https://github.com/The-Velveteen-Project/EcoAgent",
        summary="A useful repo insight.",
        evidence=["readme", "recent"],
        raw_content="",
        relevance_score=0.74,
        relevance_note="useful repo signal",
    )


async def _persist_signal(db: aiosqlite.Connection, candidate: SignalCandidate) -> int:
    return await insert_signal(db, candidate)


def test_parse_command_handles_query_and_bot_suffix() -> None:
    parsed = parse_command("/papers@velveteen_bot climate risk")
    assert parsed.name == CommandName.PAPERS
    assert parsed.query == "climate risk"


def test_parse_command_returns_unknown_for_invalid_text() -> None:
    parsed = parse_command("papers climate risk")
    assert parsed.name == CommandName.UNKNOWN


async def test_handle_command_help_returns_guide(db: aiosqlite.Connection) -> None:
    response = await handle_command("/help", db)
    assert "Velveteen commands" in response
    assert "/weekly" in response


async def test_handle_command_papers_formats_results(db: aiosqlite.Connection) -> None:
    candidate = _signal_candidate("2401.10001", "Climate risk paper")
    signal_id = await _persist_signal(db, candidate)

    with (
        patch(
            "app.services.telegram_orchestrator.discovery_service.discover",
            new=AsyncMock(return_value=[candidate]),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(return_value=_plan([signal_id], RecommendedAction.NOTE)),
        ),
    ):
        response = await handle_command("/papers climate risk", db, message_id=1)

    assert "Papers: climate risk" in response
    assert f"#{signal_id}" in response
    assert "action: <code>note</code>" in response


async def test_handle_command_github_insights_formats_results(
    db: aiosqlite.Connection,
) -> None:
    signal = SignalCandidate(
        source_type="github",
        source_id="the-velveteen-project/ecoagent::overview",
        title="EcoAgent repo signal",
        url="https://github.com/The-Velveteen-Project/EcoAgent",  # type: ignore[arg-type]
        summary="Repo signal",
        raw_content="",
        relevance_score=0.74,
        relevance_note="useful repo signal",
    )
    signal_id = await _persist_signal(db, signal)
    candidate = _github_candidate(signal.source_id, signal.title)

    with (
        patch(
            "app.services.telegram_orchestrator.github_insight_service.suggest_repo_insights",
            new=AsyncMock(return_value=[candidate]),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(return_value=_plan([signal_id], RecommendedAction.NOTE)),
        ),
    ):
        response = await handle_command("/github_insights", db, message_id=2)

    assert "GitHub insights" in response
    assert f"#{signal_id}" in response


async def test_build_weekly_summary_returns_actionable_summary(
    db: aiosqlite.Connection,
) -> None:
    external = _signal_candidate("2401.20001", "Applied research paper")
    external_id = await _persist_signal(db, external)
    github_signal = SignalCandidate(
        source_type="github",
        source_id="the-velveteen-project/ecoagent::overview",
        title="EcoAgent weekly signal",
        url="https://github.com/The-Velveteen-Project/EcoAgent",  # type: ignore[arg-type]
        summary="A GitHub portfolio signal.",
        raw_content="",
        relevance_score=0.73,
        relevance_note="repo context",
    )
    github_id = await _persist_signal(db, github_signal)
    github_candidate = _github_candidate(github_signal.source_id, github_signal.title)

    with (
        patch(
            "app.services.telegram_orchestrator.discovery_service.discover",
            new=AsyncMock(return_value=[external]),
        ),
        patch(
            "app.services.telegram_orchestrator.github_insight_service.suggest_repo_insights",
            new=AsyncMock(return_value=[github_candidate]),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(
                return_value=_plan([external_id, github_id], RecommendedAction.MVP)
            ),
        ),
    ):
        summary = await build_weekly_summary(db, query="climate risk")

    assert summary is not None
    assert summary.editorial_action == RecommendedAction.MVP
    assert summary.top_signals[0].signal_id in {external_id, github_id}
    assert "Promote" in summary.next_step


async def test_build_mvp_idea_returns_conservative_non_mvp_when_needed(
    db: aiosqlite.Connection,
) -> None:
    candidate = _signal_candidate("2401.30001", "Health modeling paper")
    signal_id = await _persist_signal(db, candidate)

    with (
        patch(
            "app.services.telegram_orchestrator.discovery_service.discover",
            new=AsyncMock(return_value=[candidate]),
        ),
        patch(
            "app.services.telegram_orchestrator.github_insight_service.suggest_repo_insights",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(return_value=_plan([signal_id], RecommendedAction.NOTE)),
        ),
    ):
        idea = await build_mvp_idea(db, "health modeling")

    assert idea.recommended_action == RecommendedAction.NOTE
    assert idea.signal_ids == [signal_id]
    assert "not strong enough" in idea.problem.lower()


async def test_handle_command_weekly_uses_summary_formatter(
    db: aiosqlite.Connection,
) -> None:
    summary = WeeklySummary(
        query="weekly focus",
        top_signals=[
            SignalSuggestion(
                signal_id=7,
                title="Signal title",
                why_it_matters="Why it matters",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.71,
            ),
        ],
        editorial_action=RecommendedAction.NOTE,
        editorial_angle="One strong angle",
        mvp_action=RecommendedAction.NOTE,
        mvp_summary="Not enough evidence for a build.",
        next_step="Turn the strongest signal into a note.",
    )

    with patch(
        "app.services.telegram_orchestrator.build_weekly_summary",
        new=AsyncMock(return_value=summary),
    ):
        response = await handle_command("/weekly", db)

    assert "Weekly summary" in response
    assert "editorial:" in response


async def test_handle_command_mvp_ideas_formats_output(
    db: aiosqlite.Connection,
) -> None:
    idea = MvpIdeaSuggestion(
        query="dengue surveillance",
        recommended_action=RecommendedAction.MVP,
        thesis="Small surveillance workflow from external signals and repo context.",
        problem="There is enough convergence to justify a narrow build.",
        why_it_matters=(
            "A small build would test the thesis better than commentary alone."
        ),
        possible_sources=["arXiv API", "GitHub REST"],
        system_type="small signal-to-build workflow with repo context",
        portfolio_fit="Fits the lab by turning signals into an applied build.",
        signal_ids=[11, 12],
    )

    with patch(
        "app.services.telegram_orchestrator.build_mvp_idea",
        new=AsyncMock(return_value=idea),
    ):
        response = await handle_command("/mvp_ideas dengue surveillance", db)

    assert "MVP idea" in response
    assert "<code>mvp</code>" in response


async def test_handle_command_plan_creates_persisted_plan(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_plan(12, signal_ids=[5], action=RecommendedAction.POST)

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.create_persisted_editorial_plan",
        new=AsyncMock(return_value=persisted),
    ):
        response = await handle_command("/plan 5", db)

    assert "Plan #12 created" in response
    assert "action: <code>post</code>" in response
    assert "next: /approve 12 or /discard_plan 12" in response


async def test_handle_command_plan_returns_not_found_for_missing_signal(
    db: aiosqlite.Connection,
) -> None:
    with patch(
        "app.services.telegram_orchestrator.editorial_planner.create_persisted_editorial_plan",
        new=AsyncMock(side_effect=LookupError("Signal not found")),
    ):
        response = await handle_command("/plan 999", db)

    assert "Signal not found" in response
    assert "<code>999</code>" in response


async def test_handle_command_approve_transitions_plan(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_plan(
        14,
        signal_ids=[5],
        action=RecommendedAction.POST,
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.transition_editorial_plan",
        new=AsyncMock(return_value=persisted),
    ):
        response = await handle_command("/approve 14", db)

    assert "Plan #14 approved" in response
    assert "status: <code>approved</code>" in response
    assert "next: /draft 14 or keep it approved" in response


async def test_handle_command_approve_returns_invalid_state(
    db: aiosqlite.Connection,
) -> None:
    with patch(
        "app.services.telegram_orchestrator.editorial_planner.transition_editorial_plan",
        new=AsyncMock(
            side_effect=EditorialPlanTransitionError(
                "Invalid editorial plan transition: saved -> approved."
            )
        ),
    ):
        response = await handle_command("/approve 14", db)

    assert "Invalid state" in response
    assert "saved -&gt; approved" in response


async def test_handle_command_discard_plan_transitions_plan(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_plan(
        15,
        signal_ids=[8],
        action=RecommendedAction.ARCHIVE,
        status=EditorialPlanStatus.DISCARDED,
    )

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.transition_editorial_plan",
        new=AsyncMock(return_value=persisted),
    ):
        response = await handle_command("/discard_plan 15", db)

    assert "Plan #15 discarded" in response
    assert "status: <code>discarded</code>" in response


async def test_handle_command_draft_creates_persisted_draft(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_draft(4, plan_id=12)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ):
        response = await handle_command("/draft 12", db)

    assert "Draft #4 created" in response
    assert "plan: <code>#12</code>" in response
    assert "title:" in response


async def test_handle_command_draft_returns_invalid_state(
    db: aiosqlite.Connection,
) -> None:
    with patch(
        "app.services.telegram_orchestrator.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(
            side_effect=DraftGenerationStateError(
                "Editorial draft generation requires an approved plan. "
                "Current status: draft."
            )
        ),
    ):
        response = await handle_command("/draft 12", db)

    assert "Invalid state" in response
    assert "requires an approved plan" in response


async def test_handle_command_draft_returns_existing_draft_conflict(
    db: aiosqlite.Connection,
) -> None:
    with patch(
        "app.services.telegram_orchestrator.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(
            side_effect=EditorialDraftConflictError(plan_id=12, draft_id=4)
        ),
    ):
        response = await handle_command("/draft 12", db)

    assert "Draft already exists" in response
    assert "draft: <code>#4</code>" in response
    assert "/show_draft 4" in response


async def test_handle_command_show_plan_formats_summary(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_plan(
        22,
        signal_ids=[5, 8],
        action=RecommendedAction.POST,
        status=EditorialPlanStatus.DRAFT,
    )

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.get_persisted_editorial_plan",
        new=AsyncMock(return_value=persisted),
    ):
        response = await handle_command("/show_plan 22", db)

    assert "<b>Plan #22</b>" in response
    assert "signals: <code>5, 8</code>" in response


async def test_handle_command_show_draft_formats_summary(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_draft(9, plan_id=22)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ):
        response = await handle_command("/show_draft 9", db)

    assert "<b>Draft #9</b>" in response
    assert "plan: <code>#22</code>" in response
    assert "short:" in response


async def test_handle_command_show_draft_returns_not_found(
    db: aiosqlite.Connection,
) -> None:
    with patch(
        "app.services.telegram_orchestrator.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(side_effect=LookupError("Draft not found")),
    ):
        response = await handle_command("/show_draft 77", db)

    assert "Draft not found" in response
    assert "<code>77</code>" in response


async def test_handle_command_invalid_plan_argument_returns_usage(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_command("/plan not-an-id", db)
    assert "<b>Usage</b>" in response
    assert "<code>/plan &lt;signal_id&gt;</code>" in response
