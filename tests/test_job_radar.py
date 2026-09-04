"""
Phase 2: job radar scoring, persistence, and pipeline commands.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiosqlite

from app.schemas.jobs import JobStatus
from app.services import job_radar
from app.services.telegram_orchestrator import handle_command, handle_operator_text
from app.utils import telegram_formatting as fmt

_HITS = [
    {
        "id": "exa-1",
        "title": "Research Engineer, Scientific Machine Learning - Remote",
        "url": "https://boards.greenhouse.io/anthropic/jobs/123",
        "publishedDate": "2026-09-01T00:00:00.000Z",
        "highlights": [
            "We build forecasting models with stochastic differential equations. "
            "Remote worldwide. PyTorch and JAX."
        ],
    },
    {
        "id": "exa-2",
        "title": "Staff Director of Sales at BigCorp",
        "url": "https://jobs.lever.co/bigcorp/999",
        "publishedDate": "2026-09-02T00:00:00.000Z",
        "highlights": ["Lead the sales org. 15+ years required."],
    },
    {
        "id": "exa-3",
        "title": "Machine Learning Engineer, Bioinformatics",
        "url": "https://jobs.ashbyhq.com/recursion/abc",
        "publishedDate": None,
        "highlights": ["Genomics and protein foundation models."],
    },
]


def _hits(tag: str) -> list[dict[str, object]]:
    """Copy of _HITS with unique urls: the test DB is shared and url is UNIQUE."""
    copies: list[dict[str, object]] = []
    for hit in _HITS:
        item = dict(hit)
        item["url"] = f"{hit['url']}?t={tag}"
        copies.append(item)
    return copies


def test_score_fit_rewards_role_domain_and_dream_company() -> None:
    score, note, dream = job_radar.score_fit(
        title="Research Engineer, Scientific Machine Learning",
        summary="stochastic forecasting, remote worldwide",
        company="Anthropic",
    )
    assert dream is True
    assert score >= 0.8
    assert "rol:" in note and "empresa objetivo: Anthropic" in note

    low, low_note, low_dream = job_radar.score_fit(
        title="Staff Director of Sales", summary="15+ years", company="BigCorp"
    )
    assert low_dream is False
    assert low == 0.0
    assert "no es un rol técnico" in low_note


def test_company_parsing_from_boards_and_titles() -> None:
    assert job_radar.company_from_url(
        "https://boards.greenhouse.io/anthropic/jobs/1"
    ) == ("Anthropic")
    assert job_radar.company_from_url("https://jobs.lever.co/hugging-face/2") == (
        "Hugging Face"
    )
    assert job_radar.company_from_url("https://example.com/careers") is None
    title, company = job_radar.split_title_company("ML Engineer at Cohere")
    assert (title, company) == ("ML Engineer", "Cohere")


async def test_run_radar_persists_new_leads_and_dedupes(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    search = AsyncMock(return_value=_hits("dedupe"))
    with patch("app.services.job_radar.exa_client.search", search):
        first = await job_radar.run_radar(db)
        second = await job_radar.run_radar(db)

    assert [lead.company for lead in first.new_leads] == ["Anthropic", "Recursion"]
    assert first.new_leads[0].dream is True
    assert first.below_fit == 1
    assert second.new_leads == []
    assert second.already_known == 2
    assert search.await_count == 2
    called_kwargs = search.await_args_list[0].kwargs
    assert "include_domains" in called_kwargs
    assert called_kwargs["start_published_date"]


async def test_run_radar_reports_failure_honestly(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1;q2")
    search = AsyncMock(side_effect=RuntimeError("EXA_API_KEY no configurada"))
    with patch("app.services.job_radar.exa_client.search", search):
        result = await job_radar.run_radar(db)
    assert result.all_failed
    text = fmt.format_job_radar(result)
    assert "Ninguna fuente respondió" in text
    assert "EXA_API_KEY" in text


async def test_jobs_command_then_apply_and_pipeline(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    with patch(
        "app.services.job_radar.exa_client.search", AsyncMock(return_value=_hits("cmd"))
    ):
        radar_text = await handle_operator_text(
            "busca vacantes de research engineer", db, chat_id=5100
        )
    assert radar_text is not None
    assert "Radar de vacantes" in radar_text
    assert "⭐" in radar_text

    leads = await job_radar.pipeline(db)
    new_ids = [lead.id for lead in leads[JobStatus.NEW]]
    assert new_ids
    target = new_ids[0]

    ack = await handle_operator_text(f"apliqué a {target} con CV v3", db, chat_id=5100)
    assert ack is not None
    assert f"Vacante #{target} → aplicadas" in ack
    assert "CV v3" in ack

    moved = await handle_command(f"/estado {target} entrevista primera ronda", db)
    assert f"Vacante #{target} → en entrevista" in moved

    pipeline_text = await handle_operator_text("pipeline", db, chat_id=5100)
    assert pipeline_text is not None
    assert "En entrevista (1)" in pipeline_text

    unknown = await handle_command("/aplicado 999999", db)
    assert "no" in unknown.lower()


async def test_job_formatters_are_valid_html(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    from tests.test_telegram_html import assert_valid_telegram_html

    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    with patch(
        "app.services.job_radar.exa_client.search",
        AsyncMock(return_value=_hits("html")),
    ):
        result = await job_radar.run_radar(db)
    assert_valid_telegram_html(fmt.format_job_radar(result, scheduled=True))
    assert_valid_telegram_html(fmt.format_pipeline(await job_radar.pipeline(db)))
    if result.new_leads:
        lead = await job_radar.mark_status(
            db, lead_id=result.new_leads[0].id, status=JobStatus.SAVED, note="x <y>"
        )
        assert_valid_telegram_html(
            fmt.format_lead_status_ack(lead, await job_radar.status_counts(db))
        )


async def test_internal_job_radar_route(client) -> None:
    with (
        patch("app.api.routes.internal.settings.internal_cron_secret", "secret-123"),
        patch("app.api.routes.internal.run_job_radar_job", new=AsyncMock()) as mock_run,
    ):
        response = await client.post(
            "/api/v1/internal/run-job-radar",
            headers={"X-Internal-Token": "secret-123"},
        )
    mock_run.assert_awaited_once()
    assert response.json()["job"] == "job_radar"


async def test_radar_shows_best_discarded_when_nothing_passes(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    from tests.test_telegram_html import assert_valid_telegram_html

    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    weak = [
        {
            "id": "w1",
            "title": "Marketing Manager <B2B>",
            "url": "https://jobs.lever.co/acme/weak-1",
            "highlights": ["Own the funnel."],
        }
    ]
    with patch(
        "app.services.job_radar.exa_client.search", AsyncMock(return_value=weak)
    ):
        result = await job_radar.run_radar(db)
    assert not result.new_leads
    assert result.below_fit == 1
    text = fmt.format_job_radar(result)
    assert "Lo mejor de lo que descarté" in text
    assert "Marketing Manager" in text
    assert_valid_telegram_html(text)


def test_company_slug_decoding_and_person_profiles_filtered() -> None:
    assert (
        job_radar.company_from_url(
            "https://boards.greenhouse.io/hippocratic%20ai/jobs/1"
        )
        == "Hippocratic AI"
    )
    assert job_radar.company_from_url("https://jobs.lever.co/mistral.ai/2") == (
        "Mistral AI"
    )
    assert job_radar.company_from_url("https://jobs.ashbyhq.com/DeepMind/3") == (
        "DeepMind"
    )
    assert job_radar.looks_like_job_posting("https://www.linkedin.com/in/someone/") is (
        False
    )
    assert job_radar.looks_like_job_posting("https://www.linkedin.com/jobs/view/1") is (
        True
    )
    assert job_radar.looks_like_job_posting(
        "https://boards.greenhouse.io/x/jobs/1"
    ) is (True)
    assert (
        job_radar.candidate_from_exa(
            {
                "id": "p",
                "title": "Abhishek Gupta",
                "url": "https://www.linkedin.com/in/ag/",
            }
        )
        is None
    )
    score, _, _ = job_radar.score_fit(
        title="AI Scientist", summary="", company="Mistral AI"
    )
    assert score >= 0.45


async def test_radar_stores_posting_text_and_enriches_inline(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    from app.schemas.jobs import JobPostingDetails
    from tests.test_telegram_html import assert_valid_telegram_html

    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    hits = _hits("enrich")
    hits[0]["text"] = "Salary $150,000 - $200,000. Remote, US only. PhD or MSc."

    class _Extractor:
        async def generate(self, *, title: str, url: str, text: str):
            assert "Salary" in text
            return JobPostingDetails(
                one_line="Build forecasting models.",
                salary_text="$150,000 - $200,000",
                salary_min_usd_year=150000,
                salary_max_usd_year=200000,
                country="United States",
                remote_policy="remote",
                location_restriction="US only",
                must_have=["MSc or PhD", "PyTorch <2.x>"],
            )

    monkeypatch.setattr(
        "app.services.job_radar.get_job_details_extractor", lambda: _Extractor()
    )
    with patch(
        "app.services.job_radar.exa_client.search", AsyncMock(return_value=hits)
    ):
        result = await job_radar.run_radar(db)

    top = result.new_leads[0]
    assert top.details is not None
    assert top.details.salary_summary() == "USD 150k–200k/año"
    assert top.details.country == "United States"
    assert job_radar.salary_verdict(top) == "por encima de tu meta"

    radar_text = fmt.format_job_radar(result)
    assert "USD 150k–200k/año" in radar_text
    assert_valid_telegram_html(radar_text)

    detail = await handle_command(f"/vacante {top.id}", db)
    assert "Piden" in detail and "MSc or PhD" in detail
    assert "por encima de tu meta" in detail
    assert_valid_telegram_html(detail)

    natural = await handle_operator_text(
        f"muéstrame la vacante {top.id}", db, chat_id=5200
    )
    assert natural is not None and "Salario" in natural


async def test_lead_detail_without_text_is_honest(db: aiosqlite.Connection) -> None:
    from app.db.queries import insert_job_lead

    lead_id, _ = await insert_job_lead(
        db,
        source="manual",
        source_id=None,
        title="Research Engineer",
        company="Acme",
        url="https://example.com/jobs/no-text-1",
        location=None,
        remote=None,
        summary="",
        published_at=None,
        fit_score=0.5,
        fit_note="rol: research engineer",
        dream=False,
    )
    detail = await handle_command(f"/vacante {lead_id}", db)
    assert "No tengo el texto de la oferta" in detail


def test_parse_lane_variants() -> None:
    assert job_radar.parse_lane(None) == ("ambos", None)
    assert job_radar.parse_lane("realista") == ("realista", None)
    assert job_radar.parse_lane("ambiciosa forecasting") == ("ambicioso", "forecasting")
    assert job_radar.parse_lane("forecasting remote") == ("ambos", "forecasting remote")


async def test_realistic_lane_skips_boards_and_dream_companies(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    boards = AsyncMock()
    monkeypatch.setattr("app.services.job_radar._run_boards", boards)
    with patch(
        "app.services.job_radar.exa_client.search",
        AsyncMock(return_value=_hits("lane-real")),
    ):
        result = await job_radar.run_radar(db, lane="realista")
    boards.assert_not_awaited()
    assert result.lane == "realista"
    assert all(not lead.dream for lead in result.new_leads)
    assert [lead.company for lead in result.new_leads] == ["Recursion"] or all(
        lead.company != "Anthropic" for lead in result.new_leads
    )
    text = fmt.format_job_radar(result)
    assert "liga realista" in text
    assert "Liga ambiciosa" not in text


async def test_ambitious_lane_skips_exa(db: aiosqlite.Connection, monkeypatch) -> None:
    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    search = AsyncMock(return_value=_hits("lane-amb"))
    monkeypatch.setattr("app.services.job_radar._run_boards", AsyncMock())
    with patch("app.services.job_radar.exa_client.search", search):
        result = await job_radar.run_radar(db, lane="ambicioso")
    search.assert_not_awaited()
    text = fmt.format_job_radar(result)
    assert "liga ambiciosa" in text
    assert "Liga realista" not in text


async def test_mood_phrase_routes_to_jobs_lane(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    monkeypatch.setattr("app.services.job_radar._run_boards", AsyncMock())
    with patch(
        "app.services.job_radar.exa_client.search",
        AsyncMock(return_value=_hits("mood")),
    ):
        text = await handle_operator_text("hoy me siento realista", db, chat_id=5300)
    assert text is not None and "liga realista" in text


async def test_pipeline_by_league_and_reset_jobs(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    from app.services import campaign as campaign_service

    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "q1")
    monkeypatch.setattr("app.services.job_radar._run_boards", AsyncMock())
    with patch(
        "app.services.job_radar.exa_client.search",
        AsyncMock(return_value=_hits("league")),
    ):
        await job_radar.run_radar(db)

    realistic = await handle_operator_text("pipeline realista", db, chat_id=5400)
    assert realistic is not None
    assert "Liga realista" in realistic
    assert "Anthropic" not in realistic and "Recursion" not in realistic
    ambitious = await handle_command("/pipeline ambicioso", db)
    assert "Liga ambiciosa" in ambitious
    assert "Anthropic" in ambitious or "Recursion" in ambitious

    ask = await handle_command("/reset_vacantes", db)
    assert "confirmar" in ask
    assert await job_radar.league_new(db, lane="realista")
    done = await handle_command("/reset_vacantes confirmar", db)
    assert "Vacantes reiniciadas" in done
    assert not await job_radar.league_new(db, lane="realista")
    assert await campaign_service.current(db) is None
