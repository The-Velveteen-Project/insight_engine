"""
Phase 1: post ledger, cadence reminder, and editorial reset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite

from app.db.queries import get_operator_state, insert_active_goal
from app.schemas.linkedin import LinkedInPost, PostStatus
from app.services import post_ledger
from app.services.telegram_orchestrator import handle_command, handle_operator_text


def _post() -> LinkedInPost:
    return LinkedInPost(
        hook="Lo que defiendo no es el modelo, son las restricciones.",
        body_paragraphs=[
            "Primer párrafo con una idea concreta.",
            "Segundo párrafo con el tradeoff.",
        ],
        closing="¿Dónde pones hoy la frontera entre interpretar y decidir?",
        hashtags=["AppliedDecisionSystems"],
    )


async def test_record_and_publish_generated_post(db: aiosqlite.Connection) -> None:
    post_id = await post_ledger.record_generated(
        db, plan_id=None, chat_id=422, post=_post(), llm_used=True, opinion_used=False
    )
    record = await post_ledger.get_record(db, post_id)
    assert record.status is PostStatus.GENERATED
    assert record.body.startswith("Lo que defiendo")
    assert record.body.endswith("#AppliedDecisionSystems")

    response = await handle_command(
        "/publicado https://www.linkedin.com/posts/cmorregof_abc", db, chat_id=422
    )
    assert "marcado como publicado" in response
    assert "linkedin.com/posts/cmorregof_abc" in response

    record = await post_ledger.get_record(db, post_id)
    assert record.status is PostStatus.PUBLISHED
    assert record.published_url == "https://www.linkedin.com/posts/cmorregof_abc"
    assert record.published_at is not None


async def test_publicado_without_generated_post_records_manual(
    db: aiosqlite.Connection,
) -> None:
    response = await handle_operator_text(
        "ya lo publiqué https://www.linkedin.com/posts/x", db, chat_id=423
    )
    assert response is not None
    assert "escrito por fuera del operador" in response
    records = await post_ledger.list_recent(db)
    manual = [r for r in records if r.chat_id == 423]
    assert manual and manual[0].status is PostStatus.PUBLISHED
    assert manual[0].plan_id is None


async def test_posts_list_shows_status_and_cadence(db: aiosqlite.Connection) -> None:
    await post_ledger.record_generated(
        db, plan_id=None, chat_id=424, post=_post(), llm_used=False, opinion_used=False
    )
    response = await handle_operator_text("mis posts", db, chat_id=424)
    assert response is not None
    assert "Posts registrados" in response
    assert "generado, sin publicar" in response
    assert "Esta semana:" in response


async def test_cadence_reminder_fires_then_respects_gap(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.post_ledger.settings.post_cadence_per_week", 2)
    monkeypatch.setattr(
        "app.services.post_ledger.settings.cadence_reminder_min_gap_hours", 60
    )
    now = datetime(2027, 1, 5, 13, 0, tzinfo=UTC)
    await post_ledger.record_generated(
        db, plan_id=None, chat_id=425, post=_post(), llm_used=False, opinion_used=False
    )

    first = await post_ledger.build_cadence_reminder(db, now)
    assert first is not None
    assert "Cadencia de LinkedIn" in first
    assert "no se han publicado" in first
    assert await get_operator_state(db, "cadence_last_reminded_at") is not None

    second = await post_ledger.build_cadence_reminder(db, now + timedelta(hours=2))
    assert second is None

    third = await post_ledger.build_cadence_reminder(db, now + timedelta(hours=61))
    assert third is not None


async def test_cadence_reminder_silent_when_target_met(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.post_ledger.settings.post_cadence_per_week", 1)
    now = datetime(2027, 1, 5, 13, 0, tzinfo=UTC)
    await post_ledger.mark_published(
        db, chat_id=426, post_id=None, url="https://www.linkedin.com/posts/y", now=now
    )
    assert await post_ledger.build_cadence_reminder(db, now) is None
    status = await post_ledger.cadence_status(db, now)
    assert status.on_track
    assert status.days_since_last(now) == 0


async def test_reset_requires_confirmation_then_restarts_ids(
    db: aiosqlite.Connection,
) -> None:
    await insert_active_goal(
        db, label="Meta de prueba", description=None, deadline_at=None
    )
    await post_ledger.record_generated(
        db, plan_id=None, chat_id=427, post=_post(), llm_used=False, opinion_used=False
    )

    ask = await handle_command("/reset_editorial", db, chat_id=427)
    assert "confirmar" in ask
    assert await post_ledger.list_recent(db)

    done = await handle_command("/reset_editorial confirmar", db, chat_id=427)
    assert "reiniciado" in done.lower()
    assert not await post_ledger.list_recent(db)

    new_id = await post_ledger.record_generated(
        db, plan_id=None, chat_id=427, post=_post(), llm_used=False, opinion_used=False
    )
    assert new_id == 1
    goal_response = await handle_command("/goal", db, chat_id=427)
    assert "Meta de prueba" in goal_response


async def test_internal_cadence_route_runs_job(client) -> None:
    with (
        patch("app.api.routes.internal.settings.internal_cron_secret", "secret-123"),
        patch(
            "app.api.routes.internal.run_cadence_reminder_job",
            new=AsyncMock(),
        ) as mock_run,
    ):
        response = await client.post(
            "/api/v1/internal/run-cadence-check",
            headers={"X-Internal-Token": "secret-123"},
        )
    mock_run.assert_awaited_once()
    assert response.status_code == 200
    assert response.json()["job"] == "cadence_check"
