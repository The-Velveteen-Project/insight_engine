"""
Tests for the minimal local scheduler.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.schemas.commands import SignalSuggestion, WeeklySummary
from app.schemas.editorial import RecommendedAction
from app.services.scheduler import (
    build_scheduler,
    next_run_after,
    parse_cron,
    run_weekly_mvp_scan_job,
    run_weekly_summary_job,
)


def test_parse_cron_weekly_expression() -> None:
    cron = parse_cron("0 9 * * 0")
    assert cron.minute == 0
    assert cron.hour == 9
    assert cron.day_of_week == 0


def test_next_run_after_weekly_expression() -> None:
    now = datetime.fromisoformat("2026-04-20T10:00:00+00:00")  # Monday
    cron = parse_cron("0 9 * * 0")  # Sunday
    scheduled = next_run_after(now, cron)
    assert scheduled.weekday() == 6
    assert scheduled.hour == 9
    assert scheduled.minute == 0


async def test_run_weekly_summary_job_sends_message(monkeypatch) -> None:
    monkeypatch.setattr("app.services.scheduler.settings.telegram_admin_chat_id", 1234)
    summary = WeeklySummary(
        query="weekly focus",
        top_signals=[
            SignalSuggestion(
                signal_id=7,
                title="Signal title",
                why_it_matters="Why it matters",
                suggested_action=RecommendedAction.NOTE,
                relevance_score=0.72,
            )
        ],
        editorial_action=RecommendedAction.NOTE,
        editorial_angle="One strong angle",
        mvp_action=RecommendedAction.NOTE,
        mvp_summary="Not enough evidence for a build.",
        next_step="Turn the strongest signal into a note.",
    )

    with (
        patch(
            "app.services.scheduler.telegram_orchestrator.build_weekly_summary",
            new=AsyncMock(return_value=summary),
        ),
        patch(
            "app.services.scheduler.send_message",
            new=AsyncMock(),
        ) as mock_send,
    ):
        await run_weekly_summary_job()

    mock_send.assert_awaited_once()
    assert mock_send.await_args.args[0] == 1234
    assert "Resumen semanal" in mock_send.await_args.args[1]


async def test_run_weekly_mvp_scan_job_sends_message(monkeypatch) -> None:
    monkeypatch.setattr("app.services.scheduler.settings.telegram_admin_chat_id", 1234)

    with (
        patch(
            "app.services.scheduler.telegram_orchestrator.build_mvp_idea",
            new=AsyncMock(
                return_value=type(
                    "Idea",
                    (),
                    {
                        "query": "weekly focus",
                        "recommended_action": RecommendedAction.NOTE,
                        "thesis": "A conservative thesis",
                        "problem": "Not enough evidence for an MVP.",
                        "why_it_matters": "Better to keep the recommendation small.",
                        "possible_sources": ["arXiv API"],
                        "system_type": "editorial and signal review workflow",
                        "portfolio_fit": "Fits the lab conservatively.",
                        "signal_ids": [1],
                    },
                )()
            ),
        ),
        patch(
            "app.services.scheduler.send_message",
            new=AsyncMock(),
        ) as mock_send,
    ):
        await run_weekly_mvp_scan_job()

    mock_send.assert_awaited_once()
    assert mock_send.await_args.args[0] == 1234
    assert "Ideas de MVP" in mock_send.await_args.args[1]


def test_build_scheduler_returns_none_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.services.scheduler.settings.enable_scheduler", False)
    assert build_scheduler() is None


def test_build_scheduler_returns_scheduler_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr("app.services.scheduler.settings.enable_scheduler", True)
    monkeypatch.setattr(
        "app.services.scheduler.settings.weekly_summary_cron",
        "0 9 * * 0",
    )
    monkeypatch.setattr(
        "app.services.scheduler.settings.weekly_mvp_scan_cron",
        "0 9 * * 4",
    )
    scheduler = build_scheduler()
    assert scheduler is not None
