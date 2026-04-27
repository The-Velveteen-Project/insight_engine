from __future__ import annotations

from unittest.mock import AsyncMock, patch


async def test_internal_weekly_summary_requires_valid_token(client) -> None:
    response = await client.post("/api/v1/internal/run-weekly-summary")
    assert response.status_code == 503
    assert response.json()["detail"] == "INTERNAL_CRON_SECRET is not configured."


async def test_internal_weekly_summary_rejects_invalid_token(client) -> None:
    with patch("app.api.routes.internal.settings.internal_cron_secret", "secret-123"):
        response = await client.post(
            "/api/v1/internal/run-weekly-summary",
            headers={"X-Internal-Token": "wrong"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid internal token."


async def test_internal_weekly_summary_runs_job(client) -> None:
    with (
        patch("app.api.routes.internal.settings.internal_cron_secret", "secret-123"),
        patch(
            "app.api.routes.internal.run_weekly_summary_job",
            new=AsyncMock(),
        ) as mock_run,
    ):
        response = await client.post(
            "/api/v1/internal/run-weekly-summary",
            headers={"X-Internal-Token": "secret-123"},
        )

    mock_run.assert_awaited_once()
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "job": "weekly_summary",
        "processed": None,
    }


async def test_internal_mvp_scan_runs_job(client) -> None:
    with (
        patch("app.api.routes.internal.settings.internal_cron_secret", "secret-123"),
        patch(
            "app.api.routes.internal.run_weekly_mvp_scan_job",
            new=AsyncMock(),
        ) as mock_run,
    ):
        response = await client.post(
            "/api/v1/internal/run-mvp-scan",
            headers={"X-Internal-Token": "secret-123"},
        )

    mock_run.assert_awaited_once()
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "job": "weekly_mvp_scan",
        "processed": None,
    }
