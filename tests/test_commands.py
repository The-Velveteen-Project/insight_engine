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
    WeeklySourceStats,
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
from app.services import telegram_orchestrator as orchestrator_module
from app.services.discovery_service import DiscoveryResult
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


async def test_handle_command_start_returns_velveteen_onboarding(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_command("/start", db)
    assert "🐇" in response
    assert "Hola, Carlos" in response
    assert "Qué soy" in response
    assert "Qué no soy" in response
    assert "Cómo usarme" in response
    assert "Lo que pienso de Velveteen" in response


async def test_handle_command_papers_formats_results(db: aiosqlite.Connection) -> None:
    candidate = _signal_candidate("2401.10001", "Climate risk paper")
    signal_id = await _persist_signal(db, candidate)

    with (
        patch(
            "app.services.telegram_orchestrator.discovery_service.discover",
            new=AsyncMock(
                return_value=DiscoveryResult(
                    signals=[candidate], normalized_query="climate risk"
                )
            ),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(return_value=_plan([signal_id], RecommendedAction.NOTE)),
        ),
    ):
        response = await handle_command("/papers climate risk", db, message_id=1)

    assert "Papers · climate risk" in response
    assert f"#{signal_id}" in response
    assert "nota técnica" in response
    assert 'href="https://arxiv.org/abs/2401.10001"' in response
    assert "Si quieres, yo seguiría por aquí" in response


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
            new=AsyncMock(
                return_value=DiscoveryResult(
                    signals=[external], normalized_query="climate risk"
                )
            ),
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
            new=AsyncMock(
                return_value=DiscoveryResult(
                    signals=[candidate], normalized_query="health modeling"
                )
            ),
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

    assert "Velveteen Operator — Weekly" in response
    assert "Señales que pasaron el filtro editorial" in response
    assert "Mi lectura" in response
    assert "Por dónde seguiría yo" in response
    assert "…" not in response


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

    assert "Idea de MVP" in response
    assert "<code>mvp</code>" in response
    assert "Mi decisión hoy es" in response
    assert "Si quieres, yo seguiría por aquí" in response


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
                source_label="arXiv API",
                url="https://example.com/weak",
            )
        ],
        normalized_query="membrane technology",
    )

    assert "Resultados exploratorios" in text
    assert "Qué haría ahora" in text
    assert "plan 3" not in text
    assert 'href="https://example.com/weak"' in text


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
                source_label="Hacker News Algolia",
                url="https://example.com/first",
            ),
            SignalSuggestion(
                signal_id=9,
                title="Second signal",
                why_it_matters="Useful but mixed.",
                suggested_action=RecommendedAction.ARCHIVE,
                relevance_score=0.58,
                source_label="arXiv API",
                url="https://example.com/second",
            ),
            SignalSuggestion(
                signal_id=10,
                title="Third signal",
                why_it_matters="More technical than public.",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.56,
                source_label="GitHub REST",
                url="https://example.com/third",
            ),
        ],
    )

    assert "Señales mezcladas" in text
    assert "note" in text or "nota técnica" in text
    assert "abrir fuente" in text


def test_format_weekly_summary_is_more_explanatory() -> None:
    summary = WeeklySummary(
        query="agentic workflows climate risk",
        top_signals=[
            SignalSuggestion(
                signal_id=4,
                title="Useful signal",
                why_it_matters="A concrete applied signal with portfolio implications.",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.71,
                source_label="GitHub REST",
                url="https://example.com/useful",
            )
        ],
        editorial_action=RecommendedAction.NOTE,
        editorial_angle="Turn this into a narrow technical note.",
        mvp_action=RecommendedAction.NOTE,
        mvp_summary="There is still not enough evidence for a build.",
        next_step="Turn signal #4 into a technical note and keep the scope narrow.",
        thesis_paragraph=(
            "Esta semana hay convergencia entre tu repo y el material externo. "
            "La línea más fuerte apunta a una nota técnica."
        ),
        signals_evaluated=12,
    )

    text = telegram_formatting.format_weekly_summary(summary)

    assert "Velveteen Operator — Weekly" in text
    assert "🐇" in text
    assert "Señales que pasaron el filtro editorial" in text
    assert "(de 12 vistas)" in text
    assert "Por qué te sirve" in text
    assert "Mi lectura" in text
    assert "convergencia entre tu repo" in text
    assert "Por dónde seguiría yo" in text
    assert 'href="https://example.com/useful"' in text
    assert "…" not in text
    assert "Score:" not in text


def test_format_weekly_summary_renders_handoff_proposal_when_set() -> None:
    summary = WeeklySummary(
        query="agentic workflows applied research",
        top_signals=[
            SignalSuggestion(
                signal_id=7,
                title="Agentic AI for Science Automation",
                why_it_matters="Te da el frame conceptual para articular el ángulo.",
                suggested_action=RecommendedAction.MVP,
                relevance_score=0.90,
                source_label="arXiv API",
                url="https://example.com/paper",
            )
        ],
        editorial_action=RecommendedAction.MVP,
        editorial_angle="Construir un MVP mínimo scopeado a una semana.",
        mvp_action=RecommendedAction.MVP,
        mvp_summary="MVP is the right move.",
        next_step="Promote into a plan and scope a one-week build.",
        thesis_paragraph=(
            "Tu repo y el paper externo convergen en el mismo eje editorial."
        ),
        handoff_proposal=(
            "El paper más tu repo dan sustancia para scopear un build de una semana."
        ),
        signals_evaluated=24,
        active_goal=(
            "cliente $4k posicionando agentic workflows aplicados"
        ),
    )

    text = telegram_formatting.format_weekly_summary(summary)

    assert "Goal activo:" in text
    assert "MVP handoff" in text
    assert "¿Te lo armo en cuanto apruebes el plan?" in text
    assert "Lo que no llegó al brief" in text
    assert "Las otras 23" in text
    assert "…" not in text


def test_format_weekly_summary_omits_handoff_when_not_proposed() -> None:
    summary = WeeklySummary(
        query="agentic workflows",
        top_signals=[
            SignalSuggestion(
                signal_id=4,
                title="Note-only signal",
                why_it_matters="Útil para nota técnica acotada.",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.55,
                source_label="arXiv API",
                url="https://example.com/note",
            )
        ],
        editorial_action=RecommendedAction.NOTE,
        editorial_angle="Una nota técnica sobria.",
        mvp_action=RecommendedAction.NOTE,
        mvp_summary="Not enough evidence for a build.",
        next_step="Turn into a note.",
        thesis_paragraph="Una semana de notas, no de builds.",
        signals_evaluated=3,
    )

    text = telegram_formatting.format_weekly_summary(summary)

    assert "MVP handoff" not in text
    assert "¿Te lo armo" not in text


def test_format_weekly_summary_uses_default_thesis_when_absent() -> None:
    summary = WeeklySummary(
        query="agentic workflows",
        top_signals=[
            SignalSuggestion(
                signal_id=1,
                title="Signal",
                why_it_matters="Why.",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.5,
                source_label="arXiv API",
            )
        ],
        editorial_action=RecommendedAction.NOTE,
        editorial_angle="Una nota técnica.",
        mvp_action=RecommendedAction.NOTE,
        mvp_summary="No build.",
        next_step="Turn into note.",
    )

    text = telegram_formatting.format_weekly_summary(summary)

    assert "Mi lectura" in text
    # Falls back to a sensible non-empty paragraph, not the old one-liner
    assert "nota técnica" in text.lower()


def test_format_mvp_idea_uses_build_reading_title_when_not_mvp() -> None:
    idea = MvpIdeaSuggestion(
        query="agentic workflows climate risk",
        recommended_action=RecommendedAction.POST,
        thesis="There is a clearer editorial angle than build opportunity.",
        problem="The evidence is not strong enough for an MVP yet.",
        why_it_matters="A build here would be premature.",
        possible_sources=["GitHub REST", "arXiv API"],
        system_type="editorial and signal review workflow",
        portfolio_fit="Better as a note or post for now.",
        signal_ids=[1],
        supporting_signals=[
            SignalSuggestion(
                signal_id=1,
                title="Repo signal",
                why_it_matters="Concrete but still not enough for a build.",
                suggested_action=RecommendedAction.POST,
                relevance_score=0.58,
                source_label="GitHub REST",
                url="https://example.com/repo",
            )
        ],
    )

    text = telegram_formatting.format_mvp_idea(idea)

    assert "Lectura de build" in text
    assert "Mi decisión hoy es" in text
    assert "Señales que sostienen esta lectura" in text
    assert 'href="https://example.com/repo"' in text
    assert "Encaje con Velveteen" in text


def test_format_plan_summary_shows_angle_not_only_why() -> None:
    persisted = _persisted_plan(
        70,
        signal_ids=[5, 8],
        action=RecommendedAction.POST,
        status=EditorialPlanStatus.DRAFT,
    )

    text = telegram_formatting.format_plan_summary(persisted)

    assert "Ángulo propuesto" in text
    assert "Use the signal to frame one concrete technical angle" in text


async def test_handle_command_plan_creates_persisted_plan(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_plan(12, signal_ids=[5], action=RecommendedAction.POST)

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.create_persisted_editorial_plan",
        new=AsyncMock(return_value=persisted),
    ):
        response = await handle_command("/plan 5", db)

    assert "Plan #12" in response
    assert "post" in response
    assert "discard_plan 12" in response


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

    assert "Plan #14" in response
    assert "<code>approved</code>" in response
    assert "draft 14" in response


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

    assert "Plan #15" in response
    assert "<code>discarded</code>" in response


async def test_handle_command_draft_creates_persisted_draft(
    db: aiosqlite.Connection,
) -> None:
    persisted = _persisted_draft(4, plan_id=12)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(return_value=persisted),
    ):
        response = await handle_command("/draft 12", db)

    assert "Draft #4" in response
    assert "plan #12" in response
    assert "muéstramelo" in response


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
    assert "#5" in response and "#8" in response


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
    assert "plan #22" in response
    assert "muéstramelo" in response


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

    assert "MVP handoff listo" in response
    assert "<code>#17</code>" in response
    assert "Builder:" in response


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
    assert "quieras" in response


async def test_handle_operator_text_accepts_bare_signals(
    db: aiosqlite.Connection,
) -> None:
    candidate = _signal_candidate("2401.40001", "Signal from bare command")
    signal_id = await _persist_signal(db, candidate)

    with (
        patch(
            "app.services.telegram_orchestrator.discovery_service.discover",
            new=AsyncMock(
                return_value=DiscoveryResult(
                    signals=[candidate], normalized_query="climate risk"
                )
            ),
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
    assert "climate risk" in response
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
            new=AsyncMock(
                return_value=DiscoveryResult(
                    signals=[first, second], normalized_query="climate risk"
                )
            ),
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

    mock_create.assert_awaited_once_with(db, [first_id], goal_id=None)
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
            new=AsyncMock(
                return_value=DiscoveryResult(
                    signals=[first, second], normalized_query="climate risk"
                )
            ),
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

    mock_create.assert_awaited_once_with(db, [first_id], goal_id=None)
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

    mock_create.assert_awaited_once_with(db, 45, goal_id=None)
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
            new=AsyncMock(
                return_value=DiscoveryResult(
                    signals=[candidate], normalized_query="climate risk"
                )
            ),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(return_value=_plan([signal_id], RecommendedAction.NOTE)),
        ),
    ):
        await handle_operator_text("signals climate risk", db, chat_id=707)

    response = await handle_operator_text("qué sigue", db, chat_id=707)
    assert response is not None
    assert "Siguiente paso" in response
    assert f"<code>#{signal_id}</code>" in response


async def test_handle_operator_text_recovers_pending_state_from_db(
    db: aiosqlite.Connection,
) -> None:
    candidate = _signal_candidate("2401.60011", "Signal for db-backed memory")
    signal_id = await _persist_signal(db, candidate)

    with (
        patch(
            "app.services.telegram_orchestrator.discovery_service.discover",
            new=AsyncMock(
                return_value=DiscoveryResult(
                    signals=[candidate], normalized_query="climate risk"
                )
            ),
        ),
        patch(
            "app.services.telegram_orchestrator._plan_for_signal_ids",
            new=AsyncMock(return_value=_plan([signal_id], RecommendedAction.NOTE)),
        ),
    ):
        await handle_operator_text("signals climate risk", db, chat_id=807)

    orchestrator_module._CHAT_STATE.clear()

    response = await handle_operator_text("qué sigue", db, chat_id=807)

    assert response is not None
    assert "Siguiente paso" in response
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
    assert "Draft #13 — versión corta" in response
    assert "CTA sugerido" in response


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
    assert "Sin acción pendiente" in response


async def test_handle_command_unknown_returns_soft_guidance(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_command("/esto_no_existe", db)
    assert "No entendí" in response
    assert "signals" in response


def test_compact_text_never_appends_ellipsis() -> None:
    long_text = (
        "Scientific workflow systems automate execution including scheduling, "
        "fault tolerance, and resource management. This is a long abstract "
        "that historically would have been chopped at a hard character limit."
    )
    out = telegram_formatting.compact_text(long_text, 80)
    assert "…" not in out
    assert "..." not in out


def test_compact_text_clips_at_sentence_boundary_when_possible() -> None:
    text = (
        "Primera frase corta. Segunda frase también razonable. "
        "Tercera frase que ya no entra dentro del límite establecido."
    )
    out = telegram_formatting.compact_text(text, 60)
    assert out.endswith(".")
    assert "Primera frase corta." in out
    assert "Tercera" not in out


def test_compact_text_falls_back_to_word_boundary() -> None:
    text = (
        "Una sola oración muy larga sin signos de puntuación intermedios "
        "que no tiene fronteras de oración dentro del límite que vamos a usar"
    )
    out = telegram_formatting.compact_text(text, 60)
    assert "…" not in out
    assert not out.endswith(" ")
    assert " " in out  # ended at a word boundary, not mid-word


def test_compact_text_returns_full_text_under_limit() -> None:
    text = "Frase corta."
    assert telegram_formatting.compact_text(text, 100) == "Frase corta."


def test_render_signal_item_omits_score_line_and_uses_te_sirve() -> None:
    suggestion = SignalSuggestion(
        signal_id=42,
        title="Some signal",
        why_it_matters="Te sirve porque conecta con tu repo.",
        suggested_action=RecommendedAction.NOTE,
        relevance_score=0.83,
        source_label="arXiv API",
        url="https://example.com/paper",
    )
    text = telegram_formatting.format_signal_suggestions(
        "Señales · test",
        [suggestion],
    )
    assert "Score:" not in text
    assert "0.83" not in text
    assert "Por qué te sirve" in text
    assert "Por qué me importa" not in text
    assert "…" not in text


async def test_handle_operator_text_returns_note_capture_ack(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_operator_text(
        "Estoy pensando en una idea sobre dengue y vigilancia.",
        db,
        chat_id=704,
    )
    assert response is not None
    assert "Registrado como nota manual" in response


# --- Sub-phase B: goal commands + handoff offer + postpone/dismiss ---------


async def test_handle_command_goal_no_args_when_no_goal(
    db: aiosqlite.Connection,
) -> None:
    from app.services import active_goals

    await active_goals.clear_active(db)

    response = await handle_command("/goal", db)
    assert "Sin goal activo" in response
    assert "/goal" in response


async def test_handle_command_goal_no_args_shows_current(
    db: aiosqlite.Connection,
) -> None:
    from app.services import active_goals

    await active_goals.clear_active(db)
    await active_goals.set_active(
        db,
        label="cliente $4k posicionando agentic workflows aplicados",
    )

    response = await handle_command("/goal", db)
    assert "Goal activo" in response
    assert "cliente $4k" in response


async def test_handle_command_goal_sets_with_deadline(
    db: aiosqlite.Connection,
) -> None:
    from app.services import active_goals

    await active_goals.clear_active(db)

    response = await handle_command(
        '/goal "cliente $4k posicionando agentic workflows" --by 2026-08-01',
        db,
    )
    assert "Goal activo actualizado" in response
    assert "cliente $4k" in response

    current = await active_goals.get_current(db)
    assert current is not None
    assert current.label == "cliente $4k posicionando agentic workflows"
    assert current.deadline_at is not None
    assert current.deadline_at.date().isoformat() == "2026-08-01"


async def test_handle_command_goal_invalid_returns_usage(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_command('/goal "x" --by not-a-date', db)
    assert "Usage" in response


async def test_handle_command_clear_goal_archives(
    db: aiosqlite.Connection,
) -> None:
    from app.services import active_goals

    await active_goals.set_active(db, label="goal a borrar pronto")

    response = await handle_command("/clear_goal", db)
    assert "Goal archivado" in response
    assert await active_goals.get_current(db) is None


async def test_handle_command_approve_mvp_appends_handoff_offer(
    db: aiosqlite.Connection,
) -> None:
    approved = _persisted_plan(
        88,
        signal_ids=[5],
        action=RecommendedAction.MVP,
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.transition_editorial_plan",
        new=AsyncMock(return_value=approved),
    ):
        response = await handle_command("/approve 88", db, chat_id=801)

    assert "Plan #88 approved" in response
    assert "MVP handoff" in response
    assert "Responde:" in response
    assert "después" in response


async def test_handle_command_approve_post_does_not_append_handoff_offer(
    db: aiosqlite.Connection,
) -> None:
    approved = _persisted_plan(
        89,
        signal_ids=[5],
        action=RecommendedAction.POST,
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.transition_editorial_plan",
        new=AsyncMock(return_value=approved),
    ):
        response = await handle_command("/approve 89", db, chat_id=802)

    assert "Plan #89 approved" in response
    assert "MVP handoff" not in response


async def test_handle_operator_text_postpone_after_mvp_approve(
    db: aiosqlite.Connection,
) -> None:
    approved = _persisted_plan(
        91,
        signal_ids=[5],
        action=RecommendedAction.MVP,
        status=EditorialPlanStatus.APPROVED,
    )

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.transition_editorial_plan",
        new=AsyncMock(return_value=approved),
    ):
        await handle_command("/approve 91", db, chat_id=803)

    response = await handle_operator_text("después", db, chat_id=803)
    assert response is not None
    assert "2 días" in response
    assert "#91" in response

    # Followup row exists for this chat.
    from app.db.queries import get_pending_handoff_followups_for_chat

    rows = await get_pending_handoff_followups_for_chat(db, 803)
    assert len(rows) == 1
    assert int(rows[0]["plan_id"]) == 91


async def test_handle_operator_text_dismiss_followup(
    db: aiosqlite.Connection,
) -> None:
    from app.services import handoff_followups

    await handoff_followups.schedule_after_postpone(
        db,
        plan_id=92,
        chat_id=804,
    )

    response = await handle_operator_text("olvídalo", db, chat_id=804)
    assert response is not None
    assert "cierro el recordatorio" in response


async def test_handle_operator_text_dismiss_when_no_followup(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_operator_text("olvídalo", db, chat_id=805)
    assert response is not None
    assert "No tengo recordatorios" in response


async def test_handle_operator_text_prefer_draft_after_mvp_approve(
    db: aiosqlite.Connection,
) -> None:
    approved = _persisted_plan(
        93,
        signal_ids=[5],
        action=RecommendedAction.MVP,
        status=EditorialPlanStatus.APPROVED,
    )
    draft = _persisted_draft(31, plan_id=93)

    with patch(
        "app.services.telegram_orchestrator.editorial_planner.transition_editorial_plan",
        new=AsyncMock(return_value=approved),
    ):
        await handle_command("/approve 93", db, chat_id=806)

    with patch(
        "app.services.telegram_orchestrator.draft_generator.create_persisted_editorial_draft",
        new=AsyncMock(return_value=draft),
    ) as mock_create:
        response = await handle_operator_text("no, mejor draft", db, chat_id=806)

    assert response is not None
    assert "Draft #31 created" in response
    mock_create.assert_awaited_once_with(db, 93, goal_id=None)


async def test_handle_operator_text_natural_goal_query(
    db: aiosqlite.Connection,
) -> None:
    from app.services import active_goals

    await active_goals.clear_active(db)
    await active_goals.set_active(db, label="goal de prueba para query natural")

    response = await handle_operator_text("cuál es mi goal", db, chat_id=807)
    assert response is not None
    assert "Goal activo" in response
    assert "goal de prueba" in response


# --- Diversity + transparency in the weekly --------------------------------


def _ref(source_type: str, source_id: str, title: str, score: float):
    """Build a _CandidateRef via the orchestrator's private type."""
    from app.services.telegram_orchestrator import _CandidateRef

    return _CandidateRef(
        source_type=source_type,
        source_id=source_id,
        title=title,
        url=f"https://example.com/{source_id}",
        summary="resumen",
        relevance_score=score,
        relevance_note="nota",
    )


def test_balanced_weekly_selection_picks_one_of_each_when_both_present() -> None:
    from app.services.telegram_orchestrator import _balanced_weekly_selection

    external = [
        _ref("arxiv", "2501.0001", "Paper externo bueno", 0.62),
    ]
    github_refs = [
        _ref("github", "g1", "Tu repo top", 0.91),
        _ref("github", "g2", "Tu repo dos", 0.88),
        _ref("github", "g3", "Tu repo tres", 0.86),
    ]
    selected = _balanced_weekly_selection(external, github_refs, target=3)
    source_types = [c.source_type for c in selected]
    assert source_types.count("arxiv") >= 1
    assert source_types.count("github") >= 1
    assert len(selected) == 3


def test_balanced_weekly_selection_falls_back_to_github_only_when_no_external() -> (
    None
):
    from app.services.telegram_orchestrator import _balanced_weekly_selection

    github_refs = [
        _ref("github", "g1", "Tu repo top", 0.91),
        _ref("github", "g2", "Tu repo dos", 0.88),
    ]
    selected = _balanced_weekly_selection([], github_refs, target=3)
    assert all(c.source_type == "github" for c in selected)
    assert len(selected) == 2


def test_format_weekly_summary_renders_discovery_footer() -> None:
    summary = WeeklySummary(
        query="test",
        top_signals=[
            SignalSuggestion(
                signal_id=1,
                title="Solo signal",
                why_it_matters="Te sirve por X.",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.6,
                source_label="GitHub REST",
                url="https://example.com/x",
            )
        ],
        editorial_action=RecommendedAction.NOTE,
        editorial_angle="Ángulo simple.",
        mvp_action=RecommendedAction.NOTE,
        mvp_summary="Sin build.",
        next_step="Nota.",
        thesis_paragraph="Tesis simple.",
        signals_evaluated=4,
        source_stats=[
            WeeklySourceStats(
                source_label="arXiv API",
                candidates_returned=4,
                candidates_in_brief=0,
                note="ninguno superó la barra editorial",
            ),
            WeeklySourceStats(
                source_label="Hacker News Algolia",
                candidates_returned=0,
                candidates_in_brief=0,
                failed=True,
                note="falla en la fuente: timeout",
            ),
            WeeklySourceStats(
                source_label="GitHub REST",
                candidates_returned=3,
                candidates_in_brief=1,
            ),
        ],
    )

    text = telegram_formatting.format_weekly_summary(summary)
    assert "Discovery esta semana" in text
    assert "arXiv API" in text
    assert "ninguno superó la barra editorial" in text
    assert "Hacker News Algolia" in text
    assert "falló" in text
    assert "GitHub REST" in text
    assert "3 candidatos" in text
    assert "1 en el brief" in text


# --- LinkedIn shipping mode ------------------------------------------------


async def test_handle_command_linkedin_renders_paste_ready_post(
    db: aiosqlite.Connection,
) -> None:
    from app.schemas.linkedin import LinkedInPost

    post = LinkedInPost(
        hook="Esta semana revisé qué hace defendible un repo aplicado.",
        body_paragraphs=[
            (
                "El primer paso fue distinguir señales de criterio "
                "de señales de actividad."
            ),
            (
                "Lo que pesa de verdad es la disciplina: tests visibles, "
                "artefactos de build."
            ),
        ],
        closing="¿Qué señal usarías tú para evaluar un repo aplicado?",
        hashtags=["AppliedAI", "AgenticWorkflows"],
    )

    with patch(
        "app.services.telegram_orchestrator.linkedin_writer.build_linkedin_post",
        new=AsyncMock(return_value=(post, True)),
    ) as mock_build:
        response = await handle_command("/linkedin 42", db)

    mock_build.assert_awaited_once_with(db, 42)
    assert "LinkedIn — plan #42" in response
    assert "Esta semana revisé" in response
    assert "AppliedAI" in response
    assert "<pre>" in response
    assert "Listo para copiar" in response
    # Hashtags prefix preserved
    assert "#AppliedAI" in response
    assert "#AgenticWorkflows" in response


async def test_handle_command_linkedin_marks_fallback_when_llm_unavailable(
    db: aiosqlite.Connection,
) -> None:
    from app.schemas.linkedin import LinkedInPost

    post = LinkedInPost(
        hook="Esta semana revisé el repo X.",
        body_paragraphs=["Razonamiento.", "Detalle."],
        closing="Cierre concreto.",
        hashtags=[],
    )

    with patch(
        "app.services.telegram_orchestrator.linkedin_writer.build_linkedin_post",
        new=AsyncMock(return_value=(post, False)),
    ):
        response = await handle_command("/linkedin 42", db)

    assert "fallback determinista" in response


def test_linkedin_fallback_does_not_stitch_template_instructions() -> None:
    """Regression: the old fallback inlined editorial *instructions* like
    'Cerrar con una implicación...' as if they were post body. The new one
    must produce a coherent skeleton flagged as a draft.
    """
    from app.schemas.editorial import EditorialSignalContext, RecommendedAction
    from app.schemas.linkedin import LinkedInPostInput
    from app.services.linkedin_writer import _fallback_post

    context = LinkedInPostInput(
        plan_id=99,
        recommended_action=RecommendedAction.NOTE,
        angle=(
            "Una nota técnica sobria que explique la lección concreta y su "
            "implicación para tu trabajo."
        ),
        why_it_matters=(
            "La señal apunta a una lección reproducible sobre evaluación "
            "de outputs en pipelines agentic."
        ),
        portfolio_value="Ayuda a sostener tu narrativa pública.",
        draft_hook="Abrir con la señal concreta.",
        draft_points=[
            "Explicar la implicación.",
            "Aclarar el tradeoff.",
        ],
        draft_closing="Cerrar con una implicación para builds futuros.",
        signals=[
            EditorialSignalContext(
                id=1,
                source_type="arxiv",
                source_id="2501.0001",
                title="Representational Harms in LLM-Generated Narratives",
                summary="A study of harms.",
                relevance_score=0.7,
                relevance_note="primary=['llm']",
            )
        ],
    )

    post = _fallback_post(context)
    assembled = (
        post.hook
        + " "
        + " ".join(post.body_paragraphs)
        + " "
        + post.closing
    ).lower()
    # The closing instruction string from draft_closing must NOT leak into
    # the assembled post — that was the old bug.
    assert "cerrar con una implicación" not in assembled
    # The fallback must self-identify as a draft so Carlos rewrites it.
    assert "borrador" in post.hook.lower() or "borrador" in post.closing.lower()


async def test_handle_command_linkedin_prompt_renders_kit(
    db: aiosqlite.Connection,
) -> None:
    from app.schemas.linkedin import LinkedInPromptKit

    kit = LinkedInPromptKit(
        plan_id=99,
        system_prompt=(
            "System prompt completo con suficientes caracteres para pasar el "
            "validador del schema y describir el rol de escritor LinkedIn."
        ),
        user_prompt=(
            "User prompt completo con bastante contexto del plan que sirve "
            "para que cualquier LLM produzca el post sin más entrada."
        ),
        one_line_paste_command=(
            "Eres mi asistente editorial. Pégalo y dame el post."
        ),
    )

    with patch(
        "app.services.telegram_orchestrator.linkedin_writer.build_linkedin_prompt_kit",
        new=AsyncMock(return_value=kit),
    ):
        response = await handle_command("/linkedin_prompt 99", db)

    assert "Prompt kit de LinkedIn — plan #99" in response
    assert "System prompt" in response
    assert "User prompt" in response
    assert "One-liner" in response
    assert "<pre>" in response


async def test_handle_operator_text_natural_linkedin_post(
    db: aiosqlite.Connection,
) -> None:
    from app.schemas.linkedin import LinkedInPost

    post = LinkedInPost(
        hook="Hook breve para natural language LinkedIn test.",
        body_paragraphs=["Cuerpo uno.", "Cuerpo dos."],
        closing="Cierre concreto y claro.",
        hashtags=[],
    )

    with patch(
        "app.services.telegram_orchestrator.linkedin_writer.build_linkedin_post",
        new=AsyncMock(return_value=(post, True)),
    ) as mock_build:
        response = await handle_operator_text(
            "armame el post de linkedin del plan 7",
            db,
            chat_id=901,
        )

    mock_build.assert_awaited_once_with(db, 7)
    assert response is not None
    assert "LinkedIn — plan #7" in response


async def test_handle_operator_text_natural_linkedin_prompt(
    db: aiosqlite.Connection,
) -> None:
    from app.schemas.linkedin import LinkedInPromptKit

    kit = LinkedInPromptKit(
        plan_id=8,
        system_prompt="x" * 100,
        user_prompt="y" * 100,
        one_line_paste_command="z" * 30,
    )

    with patch(
        "app.services.telegram_orchestrator.linkedin_writer.build_linkedin_prompt_kit",
        new=AsyncMock(return_value=kit),
    ) as mock_build:
        response = await handle_operator_text(
            "dame el prompt para linkedin del plan 8",
            db,
            chat_id=902,
        )

    mock_build.assert_awaited_once_with(db, 8)
    assert response is not None
    assert "plan #8" in response


def test_format_weekly_summary_omits_footer_when_no_stats() -> None:
    summary = WeeklySummary(
        query="test",
        top_signals=[
            SignalSuggestion(
                signal_id=1,
                title="Solo signal",
                why_it_matters="X.",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.6,
                source_label="GitHub REST",
            )
        ],
        editorial_action=RecommendedAction.NOTE,
        editorial_angle="Ángulo.",
        mvp_action=RecommendedAction.NOTE,
        mvp_summary="x",
        next_step="x",
    )
    text = telegram_formatting.format_weekly_summary(summary)
    assert "Discovery esta semana" not in text
