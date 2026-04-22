from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.integrations.telegram_client import send_message


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.telegram.org/bot/sendMessage")
            response = httpx.Response(
                self.status_code,
                json=self._payload,
                request=request,
            )
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    responses: list[_FakeResponse] = []
    sent_texts: list[str] = []

    def __init__(self, *_: object, **__: object) -> None:
        self._responses = list(type(self).responses)

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict,
        timeout: float,
    ) -> _FakeResponse:
        type(self).sent_texts.append(str(json["text"]))
        if self._responses:
            return self._responses.pop(0)
        return _FakeResponse()


@pytest.mark.asyncio
async def test_send_message_splits_long_messages(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.telegram_client.settings.telegram_max_message_chars",
        40,
    )
    _FakeAsyncClient.responses = [_FakeResponse(), _FakeResponse()]
    _FakeAsyncClient.sent_texts = []

    with patch("app.integrations.telegram_client.httpx.AsyncClient", _FakeAsyncClient):
        await send_message(123, "A" * 30 + "\n\n" + "B" * 30)

    assert len(_FakeAsyncClient.sent_texts) == 2
    assert all(len(chunk) <= 40 for chunk in _FakeAsyncClient.sent_texts)


@pytest.mark.asyncio
async def test_send_message_retries_after_429(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.integrations.telegram_client.settings.telegram_max_message_chars",
        4096,
    )
    monkeypatch.setattr(
        "app.integrations.telegram_client.settings.telegram_send_retries",
        2,
    )

    _FakeAsyncClient.responses = [
        _FakeResponse(status_code=429, payload={"parameters": {"retry_after": 0}}),
        _FakeResponse(),
    ]
    _FakeAsyncClient.sent_texts = []

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.integrations.telegram_client.asyncio.sleep", _no_sleep)

    with patch("app.integrations.telegram_client.httpx.AsyncClient", _FakeAsyncClient):
        await send_message(123, "hello")

    assert len(_FakeAsyncClient.sent_texts) == 2
