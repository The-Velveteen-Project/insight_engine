"""
Phase 3.5: Tuesday column / Thursday finding shortcut and the project brief.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.db.queries import insert_job_lead, insert_signal, set_job_lead_gap
from app.schemas.campaign import GeneratedCampaignPlan, GeneratedPlanItem, PlanItemKind
from app.schemas.cv import GapAnalysis
from app.schemas.discovery import SignalCandidate
from app.schemas.linkedin import LinkedInPost
from app.schemas.project import ProjectBrief, ProjectStage
from app.services import campaign as campaign_service
from app.services import post_ledger
from app.services.discovery_service import DiscoveryResult, DiscoverySourceOutcome
from app.services.telegram_orchestrator import (
    _split_slot_query,
    handle_command,
    handle_operator_text,
)
from tests.test_telegram_html import assert_valid_telegram_html

_MASTER = """# CV maestro

## Identity

Carlos Manuel Orrego Franco
Manizales, Colombia · cmorregofranco@gmail.com

## Experience

### CARMEN · UNAL · 2025 – present
- Forecasting engine reaches 0.78 AUROC twelve hours ahead.
"""


def _candidate(source_id: str, title: str) -> SignalCandidate:
    return SignalCandidate(
        source_type="rss",
        source_id=source_id,
        title=title,
        url=f"https://research.google/blog/{source_id}",
        summary="A foundation model for forecasting. Second sentence here.",
        relevance_score=0.7,
        relevance_note="primary=['machine learning']",
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_split_slot_query() -> None:
    assert _split_slot_query(None) == (None, None)
    assert _split_slot_query("agentes en biología") == (None, "agentes en biología")
    assert _split_slot_query("1: me parece que X") == ("1", "me parece que X")
    assert _split_slot_query("primero mi opinión") == ("primero", "mi opinión")
    assert _split_slot_query("2") == ("2", None)


async def test_column_proposal_then_one_step_post(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    candidates = [
        _candidate("tfm3", "TimesFM-3: zero-shot forecasting"),
        _candidate("glucofm", "GlucoFM: glucose foundation model"),
    ]
    for candidate in candidates:  # discovery persists signals; the mock does not
        await insert_signal(db, candidate)
    fake = DiscoveryResult(
        signals=candidates,
        normalized_query="ai agents",
        outcomes=[DiscoverySourceOutcome(source_name="rss", fetched=2)],
    )
    with patch(
        "app.services.telegram_orchestrator.discovery_service.discover",
        AsyncMock(return_value=fake),
    ):
        proposal = await handle_operator_text("columna", db, chat_id=8100)
    assert proposal is not None
    assert "Columna del martes" in proposal and "columna 1: tu opinión" in proposal
    assert_valid_telegram_html(proposal)

    post = LinkedInPost(
        hook="Three parameters beat a random forest, and this paper asks why.",
        body_paragraphs=["Uno.", "Dos."],
        closing="Which diagnostic would you use?",
        hashtags=["ScientificML"],
    )
    with patch(
        "app.services.telegram_orchestrator.linkedin_writer.build_linkedin_post",
        AsyncMock(return_value=(post, True, [("TimesFM-3", "https://x")])),
    ) as writer:
        text = await handle_operator_text(
            "columna 1: zero-shot no significa sin supuestos", db, chat_id=8100
        )
    assert text is not None
    assert "post #" in text and "plan #" in text
    assert writer.await_args.kwargs["founder_opinion"] == (
        "zero-shot no significa sin supuestos"
    )
    assert_valid_telegram_html(text)
    records = await post_ledger.list_recent(db, limit=3)
    assert records and records[0].opinion_used and records[0].plan_id is not None


async def test_finding_proposal_uses_papers(db: aiosqlite.Connection) -> None:
    fake = DiscoveryResult(
        signals=[_candidate("p1", "Latent SDEs for antigenic drift")],
        normalized_query="stochastic",
        outcomes=[DiscoverySourceOutcome(source_name="arxiv", fetched=1)],
    )
    with patch(
        "app.services.telegram_orchestrator.discovery_service.discover",
        AsyncMock(return_value=fake),
    ) as discover:
        text = await handle_command("/hallazgo neural sdes", db, chat_id=8101)
    assert "Hallazgo del jueves" in text
    names = [s.name for s in discover.await_args.kwargs["sources"]]
    assert "arxiv" in names and "hackernews" not in names


@pytest.fixture
def master_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "cv_master.md"
    path.write_text(_MASTER, encoding="utf-8")
    monkeypatch.setattr("app.services.cv_tailor.settings.cv_master_path", str(path))
    return path


async def test_project_brief_for_build_item(
    db: aiosqlite.Connection, master_file: Path, monkeypatch
) -> None:
    await post_ledger.set_operator_state(db, "cv_master", "")
    lead_id, _ = await insert_job_lead(
        db,
        source="greenhouse",
        source_id=None,
        title="Research Scientist, Life Sciences",
        company="Anthropic",
        url="https://boards.greenhouse.io/anthropic/jobs/brief-1",
        location=None,
        remote=None,
        summary="",
        published_at=None,
        fit_score=0.9,
        fit_note="rol",
        dream=True,
    )
    gap = GapAnalysis(
        verdict="stretch",
        verdict_reason="MCP servers and benchmarks are missing from the evidence.",
        covered=[],
        missing=["MCP servers for scientific data"],
        foreground=["AntigenLM audit"],
        keywords_to_mirror=[],
        opener=(
            "I audited AntigenLM representations. This role needs that plus tooling."
        ),
    )
    await set_job_lead_gap(db, lead_id=lead_id, gap_json=gap.model_dump_json())

    class _Planner:
        async def generate(self, *, system: str, user: str) -> GeneratedCampaignPlan:
            return GeneratedCampaignPlan(
                thesis="Un mes para demostrar tooling reproducible sobre AntigenLM.",
                items=[
                    GeneratedPlanItem(
                        kind=PlanItemKind.BUILD,
                        title="Servidor MCP sobre AntigenLM (repo público)",
                        why="Cierra: MCP servers for scientific data",
                        week=1,
                    ),
                    GeneratedPlanItem(
                        kind=PlanItemKind.POST,
                        title="Post: qué expone un MCP de bioinformática",
                        why="Hace visible el build 1",
                        week=2,
                    ),
                    GeneratedPlanItem(
                        kind=PlanItemKind.BUILD,
                        title="Benchmark de 20 tareas HA/NA",
                        why="Cierra: benchmarks",
                        week=3,
                    ),
                ],
            )

    monkeypatch.setattr(
        "app.services.campaign.get_campaign_planner", lambda: _Planner()
    )
    await campaign_service.start(db, lead_id)

    class _Writer:
        async def generate(self, *, system: str, user: str) -> ProjectBrief:
            assert "Servidor MCP" in user and "GAP ANALYSIS" in user
            return ProjectBrief(
                title="Servidor MCP sobre los pipelines de AntigenLM",
                objective=(
                    "Un servidor MCP público que expone las herramientas de "
                    "análisis de AntigenLM a agentes, con tests y documentación."
                ),
                closes_gap=(
                    "MCP servers for scientific data: un repo que un empleador "
                    "puede abrir."
                ),
                out_of_scope=["Entrenar modelos nuevos"],
                inputs_needed=["Ruta al repo de AntigenLM", "El texto de la brecha"],
                stages=[
                    ProjectStage(
                        name="Investigación profunda",
                        goal="Entender la spec MCP y los servidores de referencia.",
                        deep_research=["Leer la especificación oficial de MCP"],
                        deliverables=["notes/research.md"],
                        acceptance=[
                            "Las notas citan la spec y dos repos de referencia"
                        ],
                    ),
                    ProjectStage(
                        name="Diseño",
                        goal="Definir las herramientas expuestas.",
                        deliverables=["docs/design.md"],
                        acceptance=[
                            "Cada herramienta tiene esquema de entrada y salida"
                        ],
                    ),
                    ProjectStage(
                        name="Build",
                        goal="Implementar el servidor con tests.",
                        deliverables=["src/", "tests/"],
                        acceptance=["pytest pasa", "mypy strict limpio"],
                    ),
                ],
                constraints=["Núcleo determinista", "Repo público"],
                kickoff_prompt="You are helping Carlos, an applied mathematician. "
                * 20,
                post_claim=(
                    "Un MCP server de 6 herramientas hace auditables los embeddings."
                ),
            )

    monkeypatch.setattr(
        "app.services.campaign.get_project_brief_writer", lambda: _Writer()
    )
    sent = AsyncMock()
    with patch("app.services.telegram_orchestrator.send_document", sent):
        text = await handle_operator_text("proyecto 1", db, chat_id=8102)
    assert text is not None
    assert "Brief para Claude · pieza 1" in text and "Etapas:</b> 3" in text
    assert_valid_telegram_html(text)
    sent.assert_awaited_once()
    body = sent.await_args.kwargs["content"].decode("utf-8")
    assert (
        body.startswith("# Servidor MCP")
        and "## Prompt de arranque para Claude" in body
    )
    assert "Investigación profunda antes de construir" in body

    wrong = await handle_command("/proyecto 2", db)
    assert "es un post" in wrong


async def test_internal_post_proposal_route(client) -> None:
    with (
        patch("app.api.routes.internal.settings.internal_cron_secret", "secret-123"),
        patch(
            "app.api.routes.internal.run_post_proposal_job", new=AsyncMock()
        ) as mock_run,
    ):
        response = await client.post(
            "/api/v1/internal/run-post-proposal?slot=hallazgo",
            headers={"X-Internal-Token": "secret-123"},
        )
    mock_run.assert_awaited_once_with("hallazgo")
    assert response.json()["job"] == "post_proposal"
