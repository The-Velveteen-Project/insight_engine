"""
Sub-phase B.7: redirect AsyncOpenAI to any OpenAI-compatible endpoint.
"""

from __future__ import annotations

from unittest.mock import patch

from app.integrations.openai_compat import build_async_openai_client


def test_build_client_uses_default_when_base_url_blank() -> None:
    with patch(
        "app.integrations.openai_compat.settings.openai_base_url",
        "",
    ):
        client = build_async_openai_client(api_key="sk-test")
    # AsyncOpenAI exposes its base url; when not overridden it defaults to
    # the public OpenAI host. Either str or URL — coerce for the assertion.
    assert "openai.com" in str(client.base_url)


def test_build_client_honors_base_url_override() -> None:
    override = "https://openrouter.ai/api/v1"
    with patch(
        "app.integrations.openai_compat.settings.openai_base_url",
        override,
    ):
        client = build_async_openai_client(api_key="sk-test")
    base = str(client.base_url).rstrip("/")
    assert base == override


def test_build_client_strips_whitespace_in_base_url() -> None:
    with patch(
        "app.integrations.openai_compat.settings.openai_base_url",
        "   https://api.groq.com/openai/v1   ",
    ):
        client = build_async_openai_client(api_key="sk-test")
    base = str(client.base_url).rstrip("/")
    assert base == "https://api.groq.com/openai/v1"


def test_build_client_passes_timeout_when_provided() -> None:
    with patch(
        "app.integrations.openai_compat.settings.openai_base_url",
        "",
    ):
        client = build_async_openai_client(api_key="sk-test", timeout_seconds=7.5)
    # The client stores the timeout; access path varies across openai
    # versions but the value should round-trip.
    timeout = getattr(client, "timeout", None)
    if timeout is not None:
        assert float(getattr(timeout, "read", timeout)) == 7.5  # type: ignore[arg-type]
