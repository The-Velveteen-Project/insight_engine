"""
Tests for the active-goal model and the handoff follow-up service (Sub-phase B).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite

from app.db.queries import (
    get_due_handoff_followups,
    get_pending_handoff_followups_for_chat,
    insert_handoff_followup,
)
from app.schemas.editorial import (
    DecisionBasis,
    DraftOutline,
    EditorialPlan,
    EditorialPlanStatus,
    PersistedEditorialPlan,
    RecommendedAction,
)
from app.schemas.goals import HandoffRepoCandidate
from app.services import active_goals, handoff_followups


def _persisted_mvp_plan(
    plan_id: int = 9,
    *,
    angle: str = "Construir un MVP mínimo sobre stochastic green pricing.",
) -> PersistedEditorialPlan:
    timestamp = datetime.fromisoformat("2026-04-19T12:00:00")
    proposal = EditorialPlan(
        signal_ids=[1],
        recommended_action=RecommendedAction.MVP,
        decision_basis=DecisionBasis(
            primary_signal_id=1,
            supporting_signal_ids=[],
            source_types=["arxiv"],
            matched_rule="mixed_github_external_build_signal",
            confidence_factors=["base=0.30", "max_relevance=0.40"],
        ),
        why_it_matters=(
            "Te conviene porque ancla un build chico en lo que ya estás "
            "moviendo y suma señal de portafolio."
        ),
        angle=angle,
        draft_outline=DraftOutline(
            hook="Empezar por el problema concreto.",
            points=["Definir el scope mínimo", "Medir éxito chico"],
            closing="Cerrar con el experimento más pequeño.",
        ),
        portfolio_value="Convierte la señal en un artefacto auditable.",
        confidence=0.78,
        llm_used=False,
        fallback_used=True,
        needs_human_review=True,
    )
    return PersistedEditorialPlan(
        plan_id=plan_id,
        status=EditorialPlanStatus.APPROVED,
        proposal=proposal,
        created_at=timestamp,
        updated_at=timestamp,
        reviewed_at=timestamp,
    )


# --- active_goals service ---------------------------------------------------


async def test_set_active_archives_previous_goal(db: aiosqlite.Connection) -> None:
    first = await active_goals.set_active(db, label="primer goal de prueba")
    second = await active_goals.set_active(
        db,
        label="segundo goal con deadline",
        deadline_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    current = await active_goals.get_current(db)
    assert current is not None
    assert current.id == second.id
    assert current.label == "segundo goal con deadline"
    assert current.deadline_at is not None

    # Original goal still exists but is archived.
    archived = await active_goals.get_by_id(db, first.id)
    assert archived is not None
    assert archived.archived_at is not None


async def test_clear_active_archives_current(db: aiosqlite.Connection) -> None:
    await active_goals.set_active(db, label="goal a archivar pronto")
    archived = await active_goals.clear_active(db)
    assert archived is not None
    assert archived.archived_at is not None
    assert await active_goals.get_current(db) is None


async def test_seed_from_env_only_when_empty(db: aiosqlite.Connection) -> None:
    with patch(
        "app.services.active_goals.settings.active_goal_text",
        "goal seed desde env var de prueba",
    ):
        seeded = await active_goals.seed_from_env_if_empty(db)
        assert seeded is not None
        assert seeded.label == "goal seed desde env var de prueba"

        # Re-running the seed must be a no-op when a goal already exists.
        seeded_again = await active_goals.seed_from_env_if_empty(db)
        assert seeded_again is not None
        assert seeded_again.id == seeded.id


# --- handoff follow-ups: deterministic match path --------------------------


def test_deterministic_match_finds_keyword_overlap() -> None:
    plan = _persisted_mvp_plan(
        angle="Construir un MVP mínimo sobre stochastic green pricing aplicado.",
    )
    repos = [
        HandoffRepoCandidate(
            full_name="The-Velveteen-Project/StochastoGreen",
            description="Stochastic models for green pricing scenarios.",
            last_activity_summary="add stochastic baseline · refine pricing",
        ),
        HandoffRepoCandidate(
            full_name="The-Velveteen-Project/Unrelated",
            description="Something completely different.",
            last_activity_summary=None,
        ),
    ]
    match = handoff_followups._deterministic_match(plan, repos)
    assert match.match is True
    assert match.repo_full_name == "The-Velveteen-Project/StochastoGreen"


def test_deterministic_match_returns_no_match_when_no_overlap() -> None:
    plan = _persisted_mvp_plan(
        angle="Construir un MVP mínimo sobre dengue surveillance en Latinoamérica.",
    )
    repos = [
        HandoffRepoCandidate(
            full_name="The-Velveteen-Project/Unrelated",
            description="Optimal control for satellite arrays.",
            last_activity_summary="rotation calibration · drift correction",
        ),
    ]
    match = handoff_followups._deterministic_match(plan, repos)
    assert match.match is False


# --- handoff follow-ups: scheduling + dismissal ----------------------------


async def test_schedule_after_postpone_inserts_due_row(
    db: aiosqlite.Connection,
) -> None:
    chat_id = 910001
    followup_id = await handoff_followups.schedule_after_postpone(
        db,
        plan_id=42,
        chat_id=chat_id,
    )
    assert followup_id > 0

    pending = await get_pending_handoff_followups_for_chat(db, chat_id)
    assert len(pending) == 1
    assert int(pending[0]["plan_id"]) == 42
    assert pending[0]["status"] == "pending"


async def test_dismiss_latest_for_chat_marks_dismissed(
    db: aiosqlite.Connection,
) -> None:
    chat_id = 910002
    await handoff_followups.schedule_after_postpone(
        db, plan_id=42, chat_id=chat_id
    )
    dismissed = await handoff_followups.dismiss_latest_for_chat(db, chat_id)
    assert dismissed is True

    rows = await get_pending_handoff_followups_for_chat(db, chat_id)
    assert rows == []


async def test_dismiss_latest_for_chat_returns_false_when_none(
    db: aiosqlite.Connection,
) -> None:
    dismissed = await handoff_followups.dismiss_latest_for_chat(db, 910003)
    assert dismissed is False


async def test_get_due_handoff_followups_only_returns_overdue(
    db: aiosqlite.Connection,
) -> None:
    chat_id = 910004
    past = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
    future = (datetime.now(tz=UTC) + timedelta(hours=24)).isoformat()
    await insert_handoff_followup(db, plan_id=701, chat_id=chat_id, due_at=past)
    await insert_handoff_followup(db, plan_id=702, chat_id=chat_id, due_at=future)

    due = await get_due_handoff_followups(db)
    pairs = {(int(row["chat_id"]), int(row["plan_id"])) for row in due}
    assert (chat_id, 701) in pairs
    assert (chat_id, 702) not in pairs


# --- handoff follow-ups: process_due_followups end-to-end ------------------


async def test_process_due_followups_sends_match_message(
    db: aiosqlite.Connection,
) -> None:
    chat_id = 910005
    plan = _persisted_mvp_plan(plan_id=77)
    past = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
    await insert_handoff_followup(db, plan_id=77, chat_id=chat_id, due_at=past)

    repo_candidates = [
        HandoffRepoCandidate(
            full_name="The-Velveteen-Project/StochastoGreen",
            description="Stochastic green pricing models.",
            last_activity_summary="stochastic baseline added",
        ),
    ]

    with (
        patch(
            "app.services.handoff_followups.get_persisted_editorial_plan",
            new=AsyncMock(return_value=plan),
        ),
        patch(
            "app.services.handoff_followups._build_repo_candidates",
            new=AsyncMock(return_value=repo_candidates),
        ),
        patch(
            "app.services.handoff_followups.get_handoff_matcher",
            return_value=None,  # force deterministic fallback
        ),
        patch(
            "app.services.handoff_followups.send_message",
            new=AsyncMock(),
        ) as mock_send,
    ):
        await handoff_followups.process_due_followups(db)

    # The shared test DB may carry leftover due rows from prior tests; what
    # matters is that *our* chat received exactly one message with the right
    # framing when the planner answered MVP.
    sent_to_our_chat = [
        call for call in mock_send.await_args_list if call.args[0] == chat_id
    ]
    assert len(sent_to_our_chat) == 1
    text = sent_to_our_chat[0].args[1]
    assert "MVP" in text
    assert "StochastoGreen" in text


async def test_process_due_followups_skips_if_plan_missing(
    db: aiosqlite.Connection,
) -> None:
    chat_id = 910006
    past = (datetime.now(tz=UTC) - timedelta(hours=1)).isoformat()
    await insert_handoff_followup(db, plan_id=99999, chat_id=chat_id, due_at=past)

    with (
        patch(
            "app.services.handoff_followups.get_persisted_editorial_plan",
            new=AsyncMock(side_effect=LookupError("plan not found")),
        ),
        patch(
            "app.services.handoff_followups._build_repo_candidates",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.handoff_followups.send_message",
            new=AsyncMock(),
        ) as mock_send,
    ):
        await handoff_followups.process_due_followups(db)

    # No message should have been sent to this specific chat — the missing
    # plan was dismissed silently.
    sent_to_our_chat = [
        call for call in mock_send.await_args_list if call.args[0] == chat_id
    ]
    assert sent_to_our_chat == []
