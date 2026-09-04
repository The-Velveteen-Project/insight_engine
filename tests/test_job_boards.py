"""
Direct board readers (Greenhouse, Ashby) feeding the job radar.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiosqlite

from app.integrations import job_boards
from app.services import job_radar
from app.utils import telegram_formatting as fmt

_GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 5183051008,
            "title": "Anthropic Fellows Program, ML Systems & Reinforcement Learning",
            "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/5183051008",
            "location": {"name": "London, UK; Remote-Friendly, United States"},
            "departments": [{"name": "AI Research & Engineering"}],
            "first_published": "2026-04-09T20:30:30-04:00",
        },
        {
            "id": 4935314008,
            "title": "Recruiter, AI Research",
            "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/4935314008",
            "location": {"name": "San Francisco, CA"},
            "departments": [],
            "first_published": None,
        },
        {
            "id": 5297059008,
            "title": "Engineering Manager, Research Data Platform",
            "absolute_url": "https://job-boards.greenhouse.io/anthropic/jobs/5297059008",
            "location": {"name": "San Francisco, CA"},
        },
    ]
}

_ASHBY_PAYLOAD = {
    "jobs": [
        {
            "id": "941bad28",
            "title": "Researcher, Alignment",
            "jobUrl": "https://jobs.ashbyhq.com/openai/941bad28",
            "location": "San Francisco",
            "secondaryLocations": [{"location": "London, UK"}],
            "isListed": True,
            "isRemote": None,
            "department": "Research",
            "publishedAt": "2024-08-27T16:33:01.117+00:00",
            "descriptionPlain": (
                "About the Team. Alignment research on large language models "
                "with reinforcement learning. PyTorch."
            ),
        },
        {
            "id": "hidden",
            "title": "Research Scientist",
            "jobUrl": "https://jobs.ashbyhq.com/openai/hidden",
            "location": "San Francisco",
            "isListed": False,
            "descriptionPlain": "not listed",
        },
        {
            "id": "sales",
            "title": "Account Executive, Research Sales",
            "jobUrl": "https://jobs.ashbyhq.com/openai/sales",
            "location": "San Francisco",
            "isListed": True,
            "descriptionPlain": "sell things",
        },
    ]
}


def test_html_to_text_handles_double_escaped_greenhouse_content() -> None:
    raw = "&lt;h2&gt;About&lt;/h2&gt;\n&lt;p&gt;We are &amp;amp; hiring.&lt;/p&gt;"
    text = job_boards.html_to_text(raw)
    assert text == "About\n\nWe are & hiring."


def test_parsers_keep_listed_postings_with_locations() -> None:
    greenhouse = job_boards.parse_greenhouse_jobs(
        _GREENHOUSE_PAYLOAD, board_slug="anthropic"
    )
    assert [p.source_id for p in greenhouse] == [
        "5183051008",
        "4935314008",
        "5297059008",
    ]
    assert greenhouse[0].remote is True
    assert greenhouse[0].department == "AI Research & Engineering"
    assert greenhouse[0].published_at is not None
    assert greenhouse[1].remote is None

    ashby = job_boards.parse_ashby_jobs(_ASHBY_PAYLOAD, board_slug="openai")
    assert [p.source_id for p in ashby] == ["941bad28", "sales"]
    assert ashby[0].location == "San Francisco; London, UK"
    assert "Alignment research" in ashby[0].text


def test_board_title_gate_keeps_research_ic_roles_only() -> None:
    assert job_radar.board_title_passes("Research Engineer, Interpretability")
    assert job_radar.board_title_passes("Anthropic Fellows Program, AI Safety")
    assert job_radar.board_title_passes("Researcher, Alignment")
    assert not job_radar.board_title_passes("Recruiter, AI Research")
    assert not job_radar.board_title_passes("Engineering Manager, Research")
    assert not job_radar.board_title_passes("Senior Software Engineer, Research")
    assert not job_radar.board_title_passes("Data Scientist, Product")
    assert not job_radar.board_title_passes("Data Scientist, Safety")
    assert not job_radar.board_title_passes("AI Support Engineer - Toronto")
    assert not job_radar.board_title_passes("Applied AI Architect, Education")
    assert not job_radar.board_title_passes("Account Executive")


async def test_run_radar_reads_boards_before_exa(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "")
    monkeypatch.setattr(
        "app.services.job_radar.settings.job_board_sources",
        "greenhouse:anthropic:Anthropic;ashby:openai:OpenAI;bogus:x:Y",
    )
    greenhouse = AsyncMock(
        return_value=job_boards.parse_greenhouse_jobs(
            _GREENHOUSE_PAYLOAD, board_slug="anthropic"
        )
    )
    content = AsyncMock(
        return_value="Fellows work on RL and ML systems. Python. Remote-Friendly."
    )
    ashby = AsyncMock(
        return_value=job_boards.parse_ashby_jobs(_ASHBY_PAYLOAD, board_slug="openai")
    )
    with (
        patch("app.services.job_radar.job_boards.fetch_greenhouse", greenhouse),
        patch("app.services.job_radar.job_boards.fetch_greenhouse_content", content),
        patch("app.services.job_radar.job_boards.fetch_ashby", ashby),
    ):
        first = await job_radar.run_radar(db)
        second = await job_radar.run_radar(db)

    assert [o.query for o in first.outcomes] == [
        "Anthropic · greenhouse",
        "OpenAI · ashby",
    ]
    assert [o.fetched for o in first.outcomes] == [1, 1]
    titles = sorted(lead.title for lead in first.new_leads)
    assert titles == [
        "Anthropic Fellows Program, ML Systems & Reinforcement Learning",
        "Researcher, Alignment",
    ]
    assert all(lead.dream for lead in first.new_leads)
    assert all(lead.has_posting_text for lead in first.new_leads)
    assert all(lead.location for lead in first.new_leads)
    fellows = next(lead for lead in first.new_leads if "Fellows" in lead.title)
    assert fellows.source == "greenhouse" and fellows.source_id == "5183051008"
    assert "empresa objetivo: Anthropic" in fellows.fit_note
    # Content is fetched only for new Greenhouse leads, once.
    assert content.await_count == 1

    assert second.new_leads == []
    assert second.already_known == 2
    assert content.await_count == 1

    text = fmt.format_job_radar(first)
    assert "2 fuentes" in text and "⭐" in text


async def test_board_failure_is_reported_and_others_continue(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    monkeypatch.setattr("app.services.job_radar.settings.job_radar_queries", "")
    monkeypatch.setattr(
        "app.services.job_radar.settings.job_board_sources",
        "greenhouse:deepmind:Google DeepMind;ashby:openai:OpenAI",
    )
    greenhouse = AsyncMock(side_effect=RuntimeError("boom 503"))
    ashby = AsyncMock(return_value=[])
    with (
        patch("app.services.job_radar.job_boards.fetch_greenhouse", greenhouse),
        patch("app.services.job_radar.job_boards.fetch_ashby", ashby),
    ):
        result = await job_radar.run_radar(db)

    assert not result.all_failed
    assert result.outcomes[0].failed and "boom 503" in (result.outcomes[0].error or "")
    text = fmt.format_job_radar(result)
    assert "Google DeepMind · greenhouse" in text
    assert "fallaron" in text


async def test_adhoc_query_skips_boards(db: aiosqlite.Connection, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.job_radar.settings.job_board_sources",
        "greenhouse:anthropic:Anthropic",
    )
    greenhouse = AsyncMock(return_value=[])
    with (
        patch("app.services.job_radar.job_boards.fetch_greenhouse", greenhouse),
        patch("app.services.job_radar.exa_client.search", AsyncMock(return_value=[])),
    ):
        await job_radar.run_radar(db, query="bioinformatics remote")
    assert greenhouse.await_count == 0
