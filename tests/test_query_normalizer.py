from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import query_normalizer


async def test_normalize_uses_cache_after_first_call(monkeypatch) -> None:
    query_normalizer._CACHE.clear()
    monkeypatch.setattr(
        "app.services.query_normalizer.settings.anthropic_api_key",
        "test-key",
    )

    create = AsyncMock(
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="membrane filtration")]
        )
    )
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))

    with patch.dict(
        "sys.modules",
        {"anthropic": SimpleNamespace(AsyncAnthropic=lambda api_key: fake_client)},
    ):
        first = await query_normalizer.normalize("tecnologia de membranas")
        second = await query_normalizer.normalize("tecnologia de membranas")

    assert first == "membrane filtration"
    assert second == "membrane filtration"
    assert create.await_count == 1


async def test_normalize_returns_raw_query_on_timeout(monkeypatch) -> None:
    query_normalizer._CACHE.clear()
    monkeypatch.setattr(
        "app.services.query_normalizer.settings.anthropic_api_key",
        "test-key",
    )
    monkeypatch.setattr(
        "app.services.query_normalizer.settings.normalizer_timeout_seconds",
        0.01,
    )

    async def _slow_create(**_: object) -> object:
        raise TimeoutError()

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=_slow_create))

    with patch.dict(
        "sys.modules",
        {"anthropic": SimpleNamespace(AsyncAnthropic=lambda api_key: fake_client)},
    ):
        normalized = await query_normalizer.normalize("risk modeling")

    assert normalized == "risk modeling"


async def test_normalize_uses_openai_when_anthropic_key_absent(monkeypatch) -> None:
    query_normalizer._CACHE.clear()
    monkeypatch.setattr("app.services.query_normalizer.settings.anthropic_api_key", "")
    monkeypatch.setattr(
        "app.services.query_normalizer.settings.openai_api_key", "sk-test"
    )
    create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="climate risk"))]
        )
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(
        "app.services.query_normalizer.build_async_openai_client",
        lambda **_: fake_client,
    )

    normalized = await query_normalizer.normalize("riesgo climático")

    assert normalized == "climate risk"
    assert create.await_count == 1


async def test_normalize_returns_raw_query_without_any_key(monkeypatch) -> None:
    query_normalizer._CACHE.clear()
    monkeypatch.setattr("app.services.query_normalizer.settings.anthropic_api_key", "")
    monkeypatch.setattr("app.services.query_normalizer.settings.openai_api_key", "")
    assert await query_normalizer.normalize("riesgo climático") == "riesgo climático"
