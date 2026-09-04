"""
Phase 3: monthly campaign and Friday recap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.db.queries import insert_job_lead, set_job_lead_gap, set_job_lead_posting_text
from app.schemas.campaign import GeneratedCampaignPlan, GeneratedPlanItem, PlanItemKind
from app.schemas.cv import GapAnalysis, RequirementEvidence
from app.schemas.jobs import JobStatus
from app.schemas.linkedin import LinkedInPost
from app.services import campaign as campaign_service
from app.services import friday_recap, job_radar, post_ledger
from app.services.telegram_orchestrator import handle_command, handle_operator_text
from app.utils import telegram_formatting as fmt
from tests.test_telegram_html import assert_valid_telegram_html

_MASTER = """# CV maestro

## Identity

Carlos Manuel Orrego Franco
Manizales, Colombia · cmorregofranco@gmail.com

## Experience

### CARMEN · UNAL · 2025 – present
- Forecasting engine reaches 0.78 AUROC twelve hours ahead.
"""


def _gap() -> GapAnalysis:
    return GapAnalysis(
        verdict="stretch",
        verdict_reason=(
            "Life-sciences research is there; MCP servers and benchmarks are not."
        ),
        covered=[
            RequirementEvidence(
                requirement="LLM agents in production",
                evidence="CARMEN LangGraph agents with deterministic overrides",
                strength="strong",
            )
        ],
        missing=[
            "MCP servers for scientific data",
            "Benchmarks for life-science agents",
        ],
        foreground=["CARMEN", "AntigenLM audit"],
        keywords_to_mirror=["life sciences"],
        opener=(
            "I built CARMEN. This role is where that discipline meets life sciences."
        ),
    )


def _plan() -> GeneratedCampaignPlan:
    return GeneratedCampaignPlan(
        thesis=(
            "En un mes: un servidor MCP público sobre AntigenLM y un benchmark pequeño."
        ),
        items=[
            GeneratedPlanItem(
                kind=PlanItemKind.BUILD,
                title="Servidor MCP sobre los pipelines de AntigenLM (repo público)",
                why="Cierra: MCP servers for scientific data",
                week=1,
            ),
            GeneratedPlanItem(
                kind=PlanItemKind.POST,
                title="Post: qué expone un MCP server de bioinformática y qué no",
                why="Hace visible el build 1",
                week=2,
            ),
            GeneratedPlanItem(
                kind=PlanItemKind.BUILD,
                title="Benchmark de 20 tareas para agentes sobre secuencias HA/NA",
                why="Cierra: Benchmarks for life-science agents",
                week=3,
            ),
        ],
    )


@pytest.fixture
def master_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "cv_master.md"
    path.write_text(_MASTER, encoding="utf-8")
    monkeypatch.setattr("app.services.cv_tailor.settings.cv_master_path", str(path))
    return path


async def _dream_lead(db: aiosqlite.Connection, tag: str) -> int:
    lead_id, _ = await insert_job_lead(
        db,
        source="greenhouse",
        source_id=None,
        title="Applied AI Engineer, Life Sciences",
        company="Anthropic",
        url=f"https://boards.greenhouse.io/anthropic/jobs/{tag}",
        location=None,
        remote=None,
        summary="",
        published_at=None,
        fit_score=0.9,
        fit_note="rol: research engineer",
        dream=True,
    )
    await set_job_lead_posting_text(
        db, lead_id=lead_id, posting_text="Build MCP servers."
    )
    await set_job_lead_gap(db, lead_id=lead_id, gap_json=_gap().model_dump_json())
    return lead_id


async def test_campaign_start_progress_done_and_apply(
    db: aiosqlite.Connection, master_file: Path, monkeypatch
) -> None:
    await post_ledger.set_operator_state(db, "cv_master", "")
    lead_id = await _dream_lead(db, "camp-1")

    class _Planner:
        async def generate(self, *, system: str, user: str) -> GeneratedCampaignPlan:
            assert "GAP ANALYSIS" in user and "MCP servers" in user
            return _plan()

    monkeypatch.setattr(
        "app.services.campaign.get_campaign_planner", lambda: _Planner()
    )
    text = await handle_operator_text(f"objetivo del mes {lead_id}", db, chat_id=7100)
    assert text is not None
    assert "Objetivo del mes fijado" in text
    assert (
        "⬜ 1." in text and "aplicación · semana 4" in text
    )  # apply item added by code
    assert_valid_telegram_html(text)

    active = await campaign_service.current(db)
    assert active is not None and active.total_count == 4

    status = await handle_command("/objetivo", db)
    assert "1/4" not in status and "0/4 piezas" in status

    done = await handle_operator_text(
        "listo el 1 https://github.com/cmorregof/antigenlm-mcp", db, chat_id=7100
    )
    assert done is not None
    assert "Pieza 1 cerrada" in done and "1/4 piezas" in done
    assert_valid_telegram_html(done)
    refreshed = await campaign_service.current(db)
    assert refreshed is not None
    first = refreshed.plan.items[0]
    assert (
        first.done
        and first.evidence_url == "https://github.com/cmorregof/antigenlm-mcp"
    )

    applied = await handle_command("/hecho 4", db)
    assert "Objetivo del mes cumplido" in applied
    assert await campaign_service.current(db) is None


async def test_campaign_status_without_active_is_honest(
    db: aiosqlite.Connection,
) -> None:
    assert await campaign_service.current(db) is None
    text = await handle_command("/objetivo", db)
    assert "No hay objetivo del mes" in text
    done = await handle_command("/hecho 2", db)
    assert "No hay objetivo del mes" in done


async def test_friday_recap_scores_the_week(
    db: aiosqlite.Connection, master_file: Path, monkeypatch
) -> None:
    now = datetime(2027, 3, 5, 21, 0, tzinfo=UTC)
    # two posts published inside the window
    for tag in ("a", "b"):
        post_id = await post_ledger.record_generated(
            db,
            plan_id=None,
            chat_id=7200,
            post=LinkedInPost(
                hook=f"Hook {tag}: three parameters beat a random forest.",
                body_paragraphs=["Uno.", "Dos."],
                closing="What diagnostic would you use?",
                hashtags=[],
            ),
            llm_used=False,
            opinion_used=False,
        )
        await post_ledger.mark_published(
            db, chat_id=7200, post_id=post_id, url=None, now=now - timedelta(days=1)
        )
    # two realistic applications and one dream campaign with a piece done
    for tag in ("r1", "r2"):
        lead_id, _ = await insert_job_lead(
            db,
            source="exa",
            source_id=None,
            title="Research Engineer",
            company="Deepgram",
            url=f"https://jobs.lever.co/deepgram/recap-{tag}",
            location=None,
            remote=True,
            summary="",
            published_at=None,
            fit_score=0.6,
            fit_note="rol",
            dream=False,
        )
        await job_radar.mark_status(
            db, lead_id=lead_id, status=JobStatus.APPLIED, now=now - timedelta(days=2)
        )
    await post_ledger.set_operator_state(db, "cv_master", "")
    dream_id = await _dream_lead(db, "camp-recap")

    class _Planner:
        async def generate(self, *, system: str, user: str) -> GeneratedCampaignPlan:
            return _plan()

    monkeypatch.setattr(
        "app.services.campaign.get_campaign_planner", lambda: _Planner()
    )
    await campaign_service.start(db, dream_id, now=now - timedelta(days=10))
    await campaign_service.mark_done(
        db, 1, evidence_url=None, now=now - timedelta(days=3)
    )
    monkeypatch.setattr(
        "app.services.friday_recap._commits_last_week",
        AsyncMock(return_value={"The-Velveteen-Project/EcoAgent": 4}),
    )

    report = await friday_recap.build_recap(db, now=now)
    assert report.score == 3.0
    assert "Semana que suma" in report.verdict
    text = fmt.format_friday_recap(report, scheduled=True)
    assert "Recap de viernes" in text
    assert "2 realistas" in text and "Objetivo del mes" in text and "EcoAgent 4" in text
    assert_valid_telegram_html(text)


async def test_friday_recap_empty_week_is_blunt(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    now = datetime(2028, 1, 7, 21, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "app.services.friday_recap._commits_last_week", AsyncMock(return_value=None)
    )
    report = await friday_recap.build_recap(db, now=now)
    assert report.score == 0.0
    assert "sin movimiento real" in report.verdict
    text = await handle_operator_text("cómo fue la semana?", db, chat_id=7300)
    assert text is not None and "Lectura honesta" in text


async def test_internal_friday_recap_route(client) -> None:
    with (
        patch("app.api.routes.internal.settings.internal_cron_secret", "secret-123"),
        patch(
            "app.api.routes.internal.run_friday_recap_job", new=AsyncMock()
        ) as mock_run,
    ):
        response = await client.post(
            "/api/v1/internal/run-friday-recap",
            headers={"X-Internal-Token": "secret-123"},
        )
    mock_run.assert_awaited_once()
    assert response.json()["job"] == "friday_recap"
