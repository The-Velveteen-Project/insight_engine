from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.integrations import rss_client

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Lab</title>
<item><title>TimesFM 3: zero-shot &amp; multivariate</title>
<link>https://research.google/blog/timesfm-3/</link>
<guid>tfm3</guid>
<description><![CDATA[<p>A <b>foundation model</b> for forecasting.
Second sentence here.</p>]]></description>
<pubDate>Tue, 02 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Incident report</title>
<link>https://openai.com/index/incident/</link>
<description>Security post-mortem.</description>
<pubDate>Wed, 03 Sep 2026 09:00:00 GMT</pubDate></item>
<item><title></title><link>https://x/skip</link></item>
</channel></rss>"""

_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom lab</title>
<entry><id>urn:1</id><title>Stochastic agents</title>
<link rel="alternate" href="https://lab.example/agents"/>
<summary>Agents with a deterministic core.</summary>
<updated>2026-09-01T12:00:00Z</updated></entry></feed>"""


def test_parse_rss_strips_html_and_dates() -> None:
    items = rss_client.parse_feed(_RSS, feed_url="https://research.google/blog/rss/")
    assert [c.title for c in items] == [
        "TimesFM 3: zero-shot & multivariate",
        "Incident report",
    ]
    first = items[0]
    assert first.source_type == "rss"
    assert first.source_id == "research.google:tfm3"
    assert "<" not in first.summary and "…" not in first.summary
    assert first.summary.startswith("A foundation model for forecasting.")
    assert first.published_at == datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def test_parse_atom_entries() -> None:
    items = rss_client.parse_feed(_ATOM, feed_url="https://lab.example/feed")
    assert len(items) == 1
    assert str(items[0].url) == "https://lab.example/agents"
    assert items[0].published_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def test_parse_invalid_xml_raises() -> None:
    with pytest.raises(RuntimeError):
        rss_client.parse_feed("<rss><channel>", feed_url="https://bad.example/")


async def test_fetch_prefers_query_matches_then_recency(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.rss_client.settings.discovery_rss_feeds",
        "https://a.example/rss,https://b.example/rss",
    )

    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str) -> _Response:
            if url.startswith("https://a."):
                return _Response(_RSS)
            raise RuntimeError("boom")

    monkeypatch.setattr("app.integrations.rss_client.httpx.AsyncClient", _Client)

    matched = await rss_client.fetch("forecasting foundation model", max_results=5)
    assert matched[0].title.startswith("TimesFM")

    recent = await rss_client.fetch("", max_results=5)
    assert recent[0].title == "Incident report"


async def test_fetch_raises_when_every_feed_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.rss_client.settings.discovery_rss_feeds",
        "https://a.example/rss",
    )

    class _Client:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str) -> object:
            raise RuntimeError("down")

    monkeypatch.setattr("app.integrations.rss_client.httpx.AsyncClient", _Client)
    with pytest.raises(RuntimeError):
        await rss_client.fetch("anything")
