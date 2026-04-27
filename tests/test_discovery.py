"""
Tests for the discovery pipeline.

All HTTP calls are mocked — no network access required.
Coverage:
  - relevance_ranker: scoring logic, recency bonus, score cap
  - arxiv_client: XML parsing → SignalCandidate
  - hn_client: JSON parsing → SignalCandidate
  - discovery_service: dedup, ranking, persistence, source-failure isolation
  - GET /api/v1/discovery/suggest: HTTP integration (mocked discovery_service)
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from app.db.queries import insert_message
from app.domain.message import Message
from app.integrations.arxiv_client import _parse_feed
from app.integrations.exa_client import (
    _parse_results as _parse_exa_results,
)
from app.integrations.hn_client import _parse_hits
from app.schemas.discovery import SignalCandidate
from app.services import relevance_ranker
from app.services.discovery_service import DiscoveryResult, _dedup, _score, discover

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)
_RECENT = _NOW - timedelta(days=2)
_OLD = _NOW - timedelta(days=30)


def _candidate(
    *,
    source_type: str = "arxiv",
    source_id: str = "test.001",
    title: str = "A paper on machine learning",
    url: str | None = None,
    summary: str = "We study machine learning.",
    published_at: datetime | None = None,
) -> SignalCandidate:
    return SignalCandidate(
        source_type=source_type,
        source_id=source_id,
        title=title,
        url=url or f"https://arxiv.org/abs/{source_id}",  # type: ignore[arg-type]
        summary=summary,
        raw_content="",
        published_at=published_at,
    )


# ---------------------------------------------------------------------------
# relevance_ranker
# ---------------------------------------------------------------------------


def test_ranker_primary_keyword_scores() -> None:
    c = _candidate(title="LLM fine-tuning", summary="Transformer architecture study.")
    score, note = relevance_ranker.rank(c)
    assert score > 0.0
    assert "primary" in note


def test_ranker_no_keywords_scores_zero() -> None:
    c = _candidate(
        title="Unrelated gardening tips",
        summary="How to grow tomatoes in your backyard.",
    )
    score, note = relevance_ranker.rank(c)
    assert score == 0.0
    assert "no keyword matches" in note


def test_ranker_recency_bonus_within_7_days() -> None:
    c = _candidate(
        title="Unrelated gardening tips",
        summary="How to grow tomatoes in your backyard.",
        published_at=_RECENT,
    )
    score, note = relevance_ranker.rank(c)
    assert score == pytest.approx(0.10)
    assert "recent" in note


def test_ranker_no_recency_bonus_for_old_items() -> None:
    c = _candidate(
        title="Unrelated gardening tips",
        summary="How to grow tomatoes in your backyard.",
        published_at=_OLD,
    )
    score, _ = relevance_ranker.rank(c)
    assert score == 0.0


def test_ranker_score_capped_at_one() -> None:
    # Pile on many primary keywords — score must never exceed 1.0
    c = _candidate(
        title=(
            "LLM machine learning neural network deep learning transformer "
            "reinforcement learning NLP agent fine-tuning RAG embedding"
        ),
        summary=(
            "Large language model agentic workflow diffusion vector database "
            "retrieval augmented generation bayesian causal time series"
        ),
        published_at=_RECENT,
    )
    score, _ = relevance_ranker.rank(c)
    assert score <= 1.0


def test_ranker_title_weight_beats_summary_only() -> None:
    """Title-only match should outscore an equivalent summary-only match."""
    c_title = _candidate(
        title="machine learning model",
        summary="An unrelated discussion about databases.",
    )
    c_summary = _candidate(
        title="An unrelated discussion about databases.",
        summary="machine learning model",
    )
    score_title, _ = relevance_ranker.rank(c_title)
    score_summary, _ = relevance_ranker.rank(c_summary)
    assert score_title >= score_summary


def test_ranker_query_bonus_rewards_requested_topic() -> None:
    c = _candidate(
        title="Forecasting health risks with Bayesian models",
        summary="Applied research for clinical operations.",
    )
    score_without_query, _ = relevance_ranker.rank(c)
    score_with_query, note = relevance_ranker.rank(c, query="health forecasting")
    assert score_with_query > score_without_query
    assert "query=" in note


# ---------------------------------------------------------------------------
# arxiv_client — XML parsing
# ---------------------------------------------------------------------------

_ARXIV_XML = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>http://arxiv.org/abs/2401.00001v2</id>
        <title>Agentic LLM Workflows for Decision Systems</title>
        <summary>We propose an agentic framework for decision support.</summary>
        <published>2024-01-15T00:00:00Z</published>
      </entry>
      <entry>
        <id>http://arxiv.org/abs/2401.00002v1</id>
        <title>Deep Learning on Time Series</title>
        <summary>A survey of deep neural network methods for forecasting.</summary>
        <published>2024-01-10T00:00:00Z</published>
      </entry>
    </feed>
    """
).encode()


def test_arxiv_parse_feed_count() -> None:
    results = _parse_feed(_ARXIV_XML)
    assert len(results) == 2


def test_arxiv_parse_feed_fields() -> None:
    results = _parse_feed(_ARXIV_XML)
    first = results[0]
    assert first.source_type == "arxiv"
    assert first.source_id == "2401.00001"  # version suffix stripped
    assert "Agentic" in first.title
    assert first.url is not None
    assert first.published_at is not None
    assert first.published_at.year == 2024


def test_arxiv_parse_canonical_url() -> None:
    results = _parse_feed(_ARXIV_XML)
    assert str(results[0].url) == "https://arxiv.org/abs/2401.00001"


# ---------------------------------------------------------------------------
# hn_client — JSON parsing
# ---------------------------------------------------------------------------

_HN_HITS = [
    {
        "objectID": "38000001",
        "title": "Ask HN: Best agentic AI tools in 2024",
        "url": "https://news.ycombinator.com/item?id=38000001",
        "story_text": "Discussion about agentic AI frameworks.",
        "created_at_i": 1700000000,
    },
    {
        "objectID": "38000002",
        "title": "Show HN: Open-source LLM benchmarking tool",
        "url": "https://github.com/example/llm-bench",
        "story_text": None,
        "created_at_i": 1700100000,
    },
]


def test_hn_parse_hits_count() -> None:
    results = _parse_hits(_HN_HITS)
    assert len(results) == 2


def test_hn_parse_hits_fields() -> None:
    results = _parse_hits(_HN_HITS)
    first = results[0]
    assert first.source_type == "hackernews"
    assert first.source_id == "38000001"
    assert "agentic" in first.title.lower()
    assert first.published_at is not None


def test_hn_parse_hits_skips_malformed() -> None:
    """Hits with no objectID or title must be skipped."""
    bad_hits = [{"objectID": "", "title": "", "url": "https://example.com"}]
    results = _parse_hits(bad_hits)
    assert results == []


# ---------------------------------------------------------------------------
# exa_client — JSON parsing
# ---------------------------------------------------------------------------

_EXA_RESULTS = [
    {
        "id": "exa-001",
        "title": "From Research Question to Scientific Workflow",
        "url": "https://example.com/paper-001",
        "publishedDate": "2026-04-22T13:24:00.000Z",
        "highlights": [
            "Scientific workflow systems automate execution. ",
            "They schedule, manage faults, and allocate resources.",
        ],
        "score": 0.91,
    },
    {
        "id": "exa-002",
        "title": "Token consumption in agentic coding tasks",
        "url": "https://example.com/paper-002",
        "publishedDate": None,
        "highlights": ["Wide adoption of AI agents drives token growth."],
        "score": 0.84,
    },
]


def test_exa_parse_results_count() -> None:
    candidates = _parse_exa_results(_EXA_RESULTS)
    assert len(candidates) == 2


def test_exa_parse_results_fields() -> None:
    candidates = _parse_exa_results(_EXA_RESULTS)
    first = candidates[0]
    assert first.source_type == "exa"
    assert first.source_id == "exa-001"
    assert "Scientific workflow systems automate execution." in first.summary
    assert first.published_at is not None
    second = candidates[1]
    assert second.published_at is None


def test_exa_parse_results_skips_missing_fields() -> None:
    bad = [
        {"id": "", "title": "ok", "url": "https://example.com"},
        {"id": "x", "title": "", "url": "https://example.com"},
        {"id": "y", "title": "ok", "url": ""},
    ]
    assert _parse_exa_results(bad) == []


def test_exa_parse_results_uses_title_when_no_highlights() -> None:
    raw = [
        {
            "id": "exa-003",
            "title": "Standalone title without highlights",
            "url": "https://example.com/x",
            "highlights": [],
        }
    ]
    candidates = _parse_exa_results(raw)
    assert len(candidates) == 1
    assert candidates[0].summary == "Standalone title without highlights"


async def test_exa_fetch_raises_when_api_key_missing(monkeypatch) -> None:
    """The orchestrator's _safe_fetch surfaces this as a 'failed' source."""
    from app.integrations import exa_client

    monkeypatch.setattr("app.integrations.exa_client.settings.exa_api_key", "")
    with pytest.raises(RuntimeError, match="EXA_API_KEY"):
        await exa_client.fetch("anything")


def test_discovery_source_registry_includes_exa_and_legacy_hn() -> None:
    """Exa is registered; HN stays available for opt-in."""
    from app.services.discovery_service import _source_registry

    registry = _source_registry()
    assert "exa" in registry
    assert "arxiv" in registry
    assert "hackernews" in registry  # opt-in path preserved


# ---------------------------------------------------------------------------
# discovery_service — unit-level (no DB, mocked clients)
# ---------------------------------------------------------------------------


def test_dedup_removes_same_url() -> None:
    a = _candidate(url="https://arxiv.org/abs/1234.0001")
    b = _candidate(url="https://arxiv.org/abs/1234.0001/")  # trailing slash
    result = _dedup([a, b])
    assert len(result) == 1


def test_dedup_keeps_different_urls() -> None:
    a = _candidate(url="https://arxiv.org/abs/1234.0001")
    b = _candidate(url="https://arxiv.org/abs/1234.0002")
    result = _dedup([a, b])
    assert len(result) == 2


def test_score_sorts_descending() -> None:
    low = _candidate(
        source_id="low",
        title="Gardening tips for tomatoes",
        summary="",
    )
    high = _candidate(
        source_id="high",
        title="LLM agentic machine learning neural network",
        summary="Transformer fine-tuning deep learning.",
    )
    result = _score([low, high])
    assert result[0].source_id == "high"
    assert result[0].relevance_score >= result[1].relevance_score


# ---------------------------------------------------------------------------
# discovery_service — integration (mocked sources, real DB fixture)
# ---------------------------------------------------------------------------


async def test_discover_persists_to_db(db: aiosqlite.Connection) -> None:
    """discover() must write to signals table and return ranked candidates."""
    before_cursor = await db.execute("SELECT COUNT(*) FROM signals")
    before_row = await before_cursor.fetchone()
    assert before_row is not None
    before_count = int(before_row[0])

    mock_candidates = [
        _candidate(
            source_id="arxiv_001",
            title="Agentic LLM for decision support",
            summary="machine learning neural network",
        ),
        _candidate(
            source_id="arxiv_002",
            title="Bayesian time series forecasting",
            summary="A probabilistic forecasting survey.",
        ),
    ]

    with (
        patch(
            "app.services.discovery_service.normalize_query",
            new=AsyncMock(return_value="machine learning"),
        ),
        patch(
            "app.services.discovery_service.arxiv_client.fetch",
            new=AsyncMock(return_value=mock_candidates),
        ),
        patch(
            "app.services.discovery_service.exa_client.fetch",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await discover("machine learning", db, limit=3)

    assert len(result.signals) == 2
    # All results should have been scored
    for r in result.signals:
        assert r.relevance_score >= 0.0
        assert r.relevance_note != ""

    # Check DB persistence
    cursor = await db.execute("SELECT COUNT(*) FROM signals")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] - before_count == 2


async def test_discover_respects_limit(db: aiosqlite.Connection) -> None:
    mock_candidates = [
        _candidate(source_id=f"x{i}", url=f"https://arxiv.org/abs/x{i}")
        for i in range(8)
    ]

    with (
        patch(
            "app.services.discovery_service.normalize_query",
            new=AsyncMock(return_value="anything"),
        ),
        patch(
            "app.services.discovery_service.arxiv_client.fetch",
            new=AsyncMock(return_value=mock_candidates),
        ),
        patch(
            "app.services.discovery_service.exa_client.fetch",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await discover("anything", db, limit=3)

    assert len(result.signals) == 3


async def test_discover_persists_message_link_when_provided(
    db: aiosqlite.Connection,
) -> None:
    message_id = await insert_message(
        db,
        Message(
            telegram_message_id=9001,
            telegram_chat_id=77,
            raw_payload='{"update_id": 1}',
            text="find signals about health AI",
        ),
    )

    with (
        patch(
            "app.services.discovery_service.normalize_query",
            new=AsyncMock(return_value="health ai"),
        ),
        patch(
            "app.services.discovery_service.arxiv_client.fetch",
            new=AsyncMock(
                return_value=[
                    _candidate(
                        source_id="2401.10001",
                        title="Health AI systems for applied risk analysis",
                    )
                ]
            ),
        ),
        patch(
            "app.services.discovery_service.exa_client.fetch",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await discover("health ai", db, limit=1, message_id=message_id)

    cursor = await db.execute(
        "SELECT message_id FROM signals WHERE source_type = 'arxiv' AND source_id = ?",
        ("2401.10001",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["message_id"] == message_id


async def test_discover_refreshes_existing_signal_instead_of_duplicating(
    db: aiosqlite.Connection,
) -> None:
    initial = _candidate(
        source_id="2401.20001",
        title="Initial title",
        summary="machine learning systems",
    )
    refreshed = _candidate(
        source_id="2401.20001",
        title="Updated title",
        summary="machine learning systems for climate risk",
    )

    with (
        patch(
            "app.services.discovery_service.normalize_query",
            new=AsyncMock(return_value="systems"),
        ),
        patch(
            "app.services.discovery_service.arxiv_client.fetch",
            new=AsyncMock(return_value=[initial]),
        ),
        patch(
            "app.services.discovery_service.exa_client.fetch",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await discover("systems", db, limit=1)

    with (
        patch(
            "app.services.discovery_service.normalize_query",
            new=AsyncMock(return_value="climate risk"),
        ),
        patch(
            "app.services.discovery_service.arxiv_client.fetch",
            new=AsyncMock(return_value=[refreshed]),
        ),
        patch(
            "app.services.discovery_service.exa_client.fetch",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await discover("climate risk", db, limit=1)

    cursor = await db.execute(
        """
        SELECT COUNT(*) AS total, MAX(title) AS latest_title
        FROM signals
        WHERE source_type = 'arxiv' AND source_id = ?
        """,
        ("2401.20001",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["total"] == 1
    assert row["latest_title"] == "Updated title"


async def test_discover_source_failure_is_isolated(db: aiosqlite.Connection) -> None:
    """If arXiv fails, HN results are still returned."""
    hn_candidate = _candidate(
        source_type="hackernews",
        source_id="hn_001",
        url="https://news.ycombinator.com/item?id=hn_001",
        title="LLM tooling discussion",
        summary="A discussion on large language model tooling.",
    )

    with (
        patch(
            "app.services.discovery_service.normalize_query",
            new=AsyncMock(return_value="llm"),
        ),
        patch(
            "app.services.discovery_service.arxiv_client.fetch",
            new=AsyncMock(side_effect=RuntimeError("network error")),
        ),
        patch(
            "app.services.discovery_service.exa_client.fetch",
            new=AsyncMock(return_value=[hn_candidate]),
        ),
    ):
        result = await discover("llm", db, limit=3)

    assert len(result.signals) == 1
    assert result.signals[0].source_id == "hn_001"


async def test_discover_deduplicates_across_sources(db: aiosqlite.Connection) -> None:
    """Same URL from both sources must appear once."""
    shared_url = "https://arxiv.org/abs/2401.99999"
    from_arxiv = _candidate(source_id="arx", url=shared_url)
    from_hn = _candidate(source_id="hn", url=shared_url, source_type="hackernews")

    with (
        patch(
            "app.services.discovery_service.normalize_query",
            new=AsyncMock(return_value="anything"),
        ),
        patch(
            "app.services.discovery_service.arxiv_client.fetch",
            new=AsyncMock(return_value=[from_arxiv]),
        ),
        patch(
            "app.services.discovery_service.exa_client.fetch",
            new=AsyncMock(return_value=[from_hn]),
        ),
    ):
        result = await discover("anything", db, limit=5)

    assert len(result.signals) == 1


# ---------------------------------------------------------------------------
# HTTP integration — GET /api/v1/discovery/suggest
# ---------------------------------------------------------------------------


async def test_suggest_endpoint_returns_200(client) -> None:
    mock_signal = SignalCandidate(
        source_type="arxiv",
        source_id="2401.00001",
        title="Agentic LLM workflows",
        url="https://arxiv.org/abs/2401.00001",  # type: ignore[arg-type]
        summary="We study agentic machine learning workflows.",
        relevance_score=0.75,
        relevance_note="primary=['llm', 'machine learning']",
    )

    with patch(
        "app.api.routes.discovery.discovery_service.discover",
        new=AsyncMock(
            return_value=DiscoveryResult(
                signals=[mock_signal], normalized_query="agentic llm"
            )
        ),
    ):
        response = await client.get(
            "/api/v1/discovery/suggest", params={"q": "agentic llm", "limit": 1}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "agentic llm"
    assert body["total_candidates"] == 1
    assert len(body["signals"]) == 1
    assert body["signals"][0]["source_id"] == "2401.00001"
    assert body["signals"][0]["relevance_score"] == pytest.approx(0.75)


async def test_suggest_endpoint_rejects_empty_query(client) -> None:
    response = await client.get(
        "/api/v1/discovery/suggest",
        params={"q": "x"},  # min_length=2
    )
    # "x" has length 1, FastAPI should return 422
    assert response.status_code == 422


async def test_suggest_endpoint_default_limit(client) -> None:
    with patch(
        "app.api.routes.discovery.discovery_service.discover",
        new=AsyncMock(return_value=DiscoveryResult(signals=[], normalized_query="")),
    ) as mock_discover:
        await client.get("/api/v1/discovery/suggest", params={"q": "machine learning"})

    # Default limit is 3
    call_kwargs = mock_discover.call_args
    assert call_kwargs.kwargs.get("limit") == 3


async def test_suggest_endpoint_passes_message_id(client) -> None:
    with patch(
        "app.api.routes.discovery.discovery_service.discover",
        new=AsyncMock(return_value=DiscoveryResult(signals=[], normalized_query="")),
    ) as mock_discover:
        await client.get(
            "/api/v1/discovery/suggest",
            params={"q": "machine learning", "message_id": 42},
        )

    call_kwargs = mock_discover.call_args
    assert call_kwargs.kwargs.get("message_id") == 42
