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
from app.schemas.mvp_handoff import MvpHandoffPack, MvpPromptBundle
from app.services.draft_generator import (
    DraftGenerationStateError,
    EditorialDraftConflictError,
)
from app.services.editorial_planner import EditorialPlanTransitionError
from app.services.telegram_orchestrator import (
    build_mvp_idea,
    build_weekly_summary,
    handle_command,
    handle_operator_text,
    parse_command,
)
from app.utils import telegram_formatting


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


def _mvp_handoff(plan_id: int) -> MvpHandoffPack:
    return MvpHandoffPack(
        plan_id=plan_id,
        signal_ids=[1, 2],
        thesis="A small signal-to-build workflow for climate risk research",
        scope_summary=(
            "Build a narrow workflow that turns signals into a scoped MVP with "
            "clear constraints, tests, and portfolio relevance."
        ),
        context_basis=[
            "shared_velveteen_context",
            "dynamic_db_snapshot",
        ],
        prompt_architect=MvpPromptBundle(
            system_prompt="Architect prompt system",
            user_prompt="Architect prompt user with enough detail to pass validation.",
        ),
        builder=MvpPromptBundle(
            system_prompt="Builder prompt system",
            user_prompt="Builder prompt user with enough detail to pass validation.",
        ),
        auditor=MvpPromptBundle(
            system_prompt="Auditor prompt system",
            user_prompt="Auditor prompt user with enough detail to pass validation.",
        ),
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
    assert "Velveteen Operator" in response
    assert "weekly" in response


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
    assert "my take:" in response


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
    assert "my take:" in response


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
    assert "my take:" in response


def test_format_signal_suggestions_is_conservative_for_weak_evidence() -> None:
    text = telegram_formatting.format_signal_suggestions(
        "Signals: weak query",
        [
            SignalSuggestion(
                signal_id=3,
                title="Weak signal",
                why_it_matters="Interesting but thin.",
                suggested_action=RecommendedAction.ARCHIVE,
                relevance_score=0.34,
            )
        ],
    )

    assert "todavía no hay base fuerte" in text
    assert "<code>archive</code>" in text


def test_format_signal_suggestions_prefers_note_when_evidence_is_mixed() -> None:
    text = telegram_formatting.format_signal_suggestions(
        "Signals: mixed query",
        [
            SignalSuggestion(
                signal_id=8,
                title="First signal",
                why_it_matters="Useful but not decisive.",
                suggested_action=RecommendedAction.POST,
                relevance_score=0.62,
            ),
            SignalSuggestion(
                signal_id=9,
                title="Second signal",
                why_it_matters="Useful but mixed.",
                suggested_action=RecommendedAction.ARCHIVE,
                relevance_score=0.58,
            ),
            SignalSuggestion(
                signal_id=10,
                title="Third signal",
                why_it_matters="More technical than public.",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.56,
            ),
        ],
    )

    assert "mezcla de señales" in text
    assert "<code>note</code>" in text


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
        new=AsyncMock(side_effect=EditorialDraftConflictError(plan_id=12, draft_id=4)),
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


async def test_handle_command_mvp_handoff_formats_summary(
    db: aiosqlite.Connection,
) -> None:
    with patch(
        "app.services.telegram_orchestrator.mvp_handoff.create_mvp_handoff_pack",
        new=AsyncMock(return_value=_mvp_handoff(17)),
    ):
        response = await handle_command("/mvp_handoff 17", db)

    assert "MVP handoff ready" in response
    assert "plan: <code>#17</code>" in response
    assert "builder:" in response


async def test_handle_operator_text_accepts_bare_help(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_operator_text("help", db, chat_id=700)
    assert response is not None
    assert "Velveteen Operator" in response


async def test_handle_operator_text_greets_naturally(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_operator_text("Hola", db, chat_id=699)
    assert response is not None
    assert "Hola, Carlos" in response
    assert "signals climate risk" in response


async def test_handle_operator_text_handles_gratitude_naturally(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_operator_text("gracias", db, chat_id=698)
    assert response is not None
    assert "De una" in response
    assert "último draft" in response


async def test_handle_operator_text_accepts_bare_signals(
    db: aiosqlite.Connection,
) -> None:
    candidate = _signal_candidate("2401.40001", "Signal from bare command")
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
        response = await handle_operator_text(
            "signals climate risk",
            db,
            chat_id=701,
        )

    assert response is not None
    assert "Signals: climate risk" in response
    assert f"#{signal_id}" in response


async def test_handle_operator_text_uses_chat_memory_for_plan_del_primero(
    db: aiosqlite.Connection,
) -> None:
    first = _signal_candidate("2401.50001", "First signal")
    second = _signal_candidate("2401.50002", "Second signal")
    first_id = await _persist_signal(db, first)
    await _persist_signal(db, second)

    with (
        patch(
            "app.services.telegram_orchestrator.discovery_service.discover",
            new=AsyncMock(return_value=[first, second]),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(return_value=_plan([first_id], RecommendedAction.NOTE)),
        ),
    ):
        await handle_operator_text("signals climate risk", db, chat_id=702)

    persisted = _persisted_plan(
        30,
        signal_ids=[first_id],
        action=RecommendedAction.NOTE,
    )
    with patch(
        "app.services.telegram_orchestrator.editorial_planner.create_persisted_editorial_plan",
        new=AsyncMock(return_value=persisted),
    ) as mock_create:
        response = await handle_operator_text(
            "hazme un plan del primero",
            db,
            chat_id=702,
        )

    mock_create.assert_awaited_once_with(db, [first_id])
    assert response is not None
    assert "Plan #30 created" in response


async def test_handle_operator_text_uses_pending_action_for_hazlo_after_signals(
    db: aiosqlite.Connection,
) -> None:
    first = _signal_candidate("2401.50011", "First signal")
    second = _signal_candidate("2401.50012", "Second signal")
    first_id = await _persist_signal(db, first)
    await _persist_signal(db, second)

    with (
        patch(
            "app.services.telegram_orchestrator.discovery_service.discover",
            new=AsyncMock(return_value=[first, second]),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(return_value=_plan([first_id], RecommendedAction.NOTE)),
        ),
    ):
        await handle_operator_text("signals climate risk", db, chat_id=705)

    persisted = _persisted_plan(
        31,
        signal_ids=[first_id],
        action=RecommendedAction.NOTE,
    )
    with patch(
        "app.services.telegram_orchestrator.editorial_planner.create_persisted_editorial_plan",
        new=AsyncMock(return_value=persisted),
    ) as mock_create:
        response = await handle_operator_text("hazlo", db, chat_id=705)

    mock_create.assert_awaited_once_with(db, [first_id])
    assert response is not None
    assert "Plan #31 created" in response


async def test_handle_operator_text_uses_chat_memory_for_apruebalo(
    db: aiosqlite.Connection,
) -> None:
    persisted_draft = _persisted_plan(
        44,
        signal_ids=[9],
        action=RecommendedAction.POST,
        status=EditorialPlanStatus.DRAFT,
    )
    persisted_approved = _persisted_plan(
        44,
        signal_ids=[9],
        action=RecommendedAction.POST,
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.get_persisted_editorial_plan",
        new=AsyncMock(return_value=persisted_draft),
    ):
        await handle_operator_text("show_plan 44", db, chat_id=703)

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.transition_editorial_plan",
        new=AsyncMock(return_value=persisted_approved),
    ) as mock_transition:
        response = await handle_operator_text("apruébalo", db, chat_id=703)

    mock_transition.assert_awaited_once_with(
        db,
        44,
        EditorialPlanStatus.APPROVED,
    )
    assert response is not None
    assert "Plan #44 approved" in response


async def test_handle_operator_text_uses_pending_action_for_hazlo_after_approved_plan(
    db: aiosqlite.Connection,
) -> None:
    approved = _persisted_plan(
        45,
        signal_ids=[9],
        action=RecommendedAction.POST,
        status=EditorialPlanStatus.APPROVED,
    )
    draft = _persisted_draft(10, plan_id=45)

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.get_persisted_editorial_plan",
        new=AsyncMock(return_value=approved),
    ):
        await handle_operator_text("show_plan 45", db, chat_id=706)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(return_value=draft),
    ) as mock_create:
        response = await handle_operator_text("hazlo", db, chat_id=706)

    mock_create.assert_awaited_once_with(db, 45)
    assert response is not None
    assert "Draft #10 created" in response


async def test_handle_operator_text_sigamos_con_eso_shows_last_draft_when_no_pending(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_draft(12, plan_id=47)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ):
        await handle_command("/show_draft 12", db, chat_id=710)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ) as mock_get:
        response = await handle_operator_text("sigamos con eso", db, chat_id=710)

    mock_get.assert_awaited_once_with(db, 12)
    assert response is not None
    assert "<b>Draft #12</b>" in response


async def test_handle_operator_text_que_sigue_returns_pending_hint(
    db: aiosqlite.Connection,
) -> None:
    candidate = _signal_candidate("2401.60001", "Signal for next step")
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
        await handle_operator_text("signals climate risk", db, chat_id=707)

    response = await handle_operator_text("qué sigue", db, chat_id=707)
    assert response is not None
    assert "Next step" in response
    assert f"<code>#{signal_id}</code>" in response


async def test_handle_operator_text_returns_short_version_for_last_draft(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_draft(13, plan_id=48)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ):
        await handle_command("/show_draft 13", db, chat_id=711)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ) as mock_get:
        response = await handle_operator_text(
            "dame una versión corta",
            db,
            chat_id=711,
        )

    mock_get.assert_awaited_once_with(db, 13)
    assert response is not None
    assert "Draft #13 — short version" in response
    assert "cta:" in response


async def test_handle_operator_text_muestramelo_prefers_last_draft(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_draft(11, plan_id=46)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ):
        await handle_command("/show_draft 11", db, chat_id=708)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.get_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ) as mock_get:
        response = await handle_operator_text("muéstramelo", db, chat_id=708)

    mock_get.assert_awaited_once_with(db, 11)
    assert response is not None
    assert "<b>Draft #11</b>" in response


async def test_handle_operator_text_hazlo_without_pending_returns_guidance(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_operator_text("hazlo", db, chat_id=709)
    assert response is not None
    assert "No pending action" in response


async def test_handle_command_unknown_returns_soft_guidance(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_command("/esto_no_existe", db)
    assert "No tomé eso como una instrucción operativa" in response
    assert "signals climate risk" in response


async def test_handle_operator_text_returns_note_capture_ack(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_operator_text(
        "Estoy pensando en una idea sobre dengue y vigilancia.",
        db,
        chat_id=704,
    )
    assert response is not None
    assert "Lo registré como señal manual" in response
