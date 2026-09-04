"""
Phase 3.7: imagen <post_id>: recommendation plus a deterministic data card.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import aiosqlite

from app.schemas.linkedin import LinkedInPost
from app.services import post_ledger, post_visuals
from app.services.telegram_orchestrator import handle_command, handle_operator_text
from tests.test_telegram_html import assert_valid_telegram_html


def test_extract_number_claim_prefers_percentages() -> None:
    body = (
        "Benchmarks saturate. The number I keep returning to is 64.6% on "
        "Terminal-Bench Science. Two out of three. #ScientificML"
    )
    claim = post_visuals.extract_number_claim(body)
    assert claim is not None
    assert claim.value_text == "64.6%" and claim.percent == 64.6
    assert claim.sentence.startswith("The number I keep returning to")

    plain = post_visuals.extract_number_claim(
        "Three parameters beat a neural net across 151 countries."
    )
    assert plain is not None and plain.value_text == "151" and plain.percent is None

    assert post_visuals.extract_number_claim("No numbers here at all.") is None


def test_render_data_card_returns_png() -> None:
    claim = post_visuals.NumberClaim("64.6%", 64.6, "Two out of three workflows.")
    png = post_visuals.render_data_card(
        title="The part that matters is not the score",
        claim=claim,
        caption="Two out of three workflows completed with code and terminal tools.",
        source="OpenAI, GPT-6 Astra",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 20_000


async def test_imagen_command_sends_card_and_recommends(
    db: aiosqlite.Connection, monkeypatch
) -> None:
    post = LinkedInPost(
        hook="The part that matters is not the score; it is what a laptop can attempt.",
        body_paragraphs=[
            "I learned at CEMRACS that three parameters can beat a neural network.",
            "The number I keep returning to is 64.6% on Terminal-Bench Science.",
        ],
        closing="What evaluation would you use?",
        hashtags=["ScientificML"],
    )
    post_id = await post_ledger.record_generated(
        db, plan_id=None, chat_id=9100, post=post, llm_used=True, opinion_used=True
    )
    sent = AsyncMock()
    with patch("app.services.telegram_orchestrator.send_photo", sent):
        text = await handle_command(f"/imagen {post_id}", db, chat_id=9100)
    sent.assert_awaited_once()
    assert sent.await_args.kwargs["content"][:4] == b"\x89PNG"
    assert "CEMRACS" in text and "64.6%" in text
    assert_valid_telegram_html(text)

    with patch("app.services.telegram_orchestrator.send_photo", AsyncMock()):
        latest = await handle_operator_text(
            "dame una imagen para el post", db, chat_id=9100
        )
    assert latest is not None and f"post #{post_id}" in latest


async def test_imagen_without_number_recommends_no_image(
    db: aiosqlite.Connection,
) -> None:
    post = LinkedInPost(
        hook="A note on constraints, written from the inside of a system.",
        body_paragraphs=["Uno sin números.", "Dos sin números."],
        closing="Where do you put the boundary?",
        hashtags=[],
    )
    post_id = await post_ledger.record_generated(
        db, plan_id=None, chat_id=9101, post=post, llm_used=False, opinion_used=False
    )
    with patch("app.services.telegram_orchestrator.send_photo", AsyncMock()) as sent:
        text = await handle_command(f"/imagen {post_id}", db, chat_id=9101)
    sent.assert_not_awaited()
    assert "Publícalo sin imagen" in text
