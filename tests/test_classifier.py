"""
Unit tests for the deterministic classifier.
No DB, no network — pure input/output.
"""

import pytest

from app.schemas.telegram import TelegramMessage
from app.services.classifier import (
    classify,
    extract_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(
    text: str | None = None,
    *,
    has_voice: bool = False,
    has_document: bool = False,
    reply_to_id: int | None = None,
) -> TelegramMessage:
    data: dict = {
        "message_id": 1,
        "from": {"id": 42, "is_bot": False, "first_name": "Test"},
        "chat": {"id": 100, "type": "private"},
        "date": 1700000000,
    }
    if text is not None:
        data["text"] = text
    if has_voice:
        data["voice"] = {
            "file_id": "abc123",
            "file_unique_id": "u123",
            "duration": 12,
        }
    if has_document:
        data["document"] = {
            "file_id": "doc123",
            "file_unique_id": "du123",
            "file_name": "report.pdf",
        }
    if reply_to_id is not None:
        data["reply_to_message"] = {
            "message_id": reply_to_id,
            "from": {"id": 99, "is_bot": False, "first_name": "Other"},
            "chat": {"id": 100, "type": "private"},
            "date": 1699990000,
            "text": "original",
        }
    return TelegramMessage.model_validate(data)


# ---------------------------------------------------------------------------
# message_type classification — priority: voice > url > reply > document > text
# ---------------------------------------------------------------------------


def test_classify_plain_text() -> None:
    c = classify(_msg("Just a plain message."))
    assert c.message_type == "text"
    assert not c.has_url
    assert not c.is_reply


def test_classify_voice() -> None:
    c = classify(_msg(has_voice=True))
    assert c.message_type == "voice"


def test_classify_url_in_text() -> None:
    c = classify(_msg("Check this: https://arxiv.org/abs/2301.07041"))
    assert c.message_type == "url"
    assert c.has_url
    assert c.source_url == "https://arxiv.org/abs/2301.07041"


def test_classify_reply_no_url() -> None:
    c = classify(_msg("Good point!", reply_to_id=5))
    assert c.message_type == "reply"
    assert c.is_reply
    assert c.reply_to_telegram_id == 5


def test_classify_document() -> None:
    c = classify(_msg(has_document=True))
    assert c.message_type == "document"


# ---------------------------------------------------------------------------
# Priority checks — orthogonal flags preserved when type is collapsed
# ---------------------------------------------------------------------------


def test_voice_beats_url() -> None:
    """Voice message with a URL in caption: type=voice, has_url=True."""
    data = {
        "message_id": 10,
        "from": {"id": 42, "is_bot": False, "first_name": "Test"},
        "chat": {"id": 100, "type": "private"},
        "date": 1700000000,
        "voice": {"file_id": "v1", "file_unique_id": "vu1", "duration": 5},
        "caption": "Listen: https://example.com",
    }
    c = classify(TelegramMessage.model_validate(data))
    assert c.message_type == "voice"
    assert c.has_url  # orthogonal flag preserved


def test_url_beats_reply() -> None:
    """Reply that also contains a URL: type=url, is_reply=True."""
    c = classify(_msg("See https://example.com", reply_to_id=3))
    assert c.message_type == "url"
    assert c.is_reply  # orthogonal flag preserved
    assert c.has_url


def test_reply_flag_independent_of_type() -> None:
    """is_reply is set regardless of what message_type ends up being."""
    c = classify(_msg("https://arxiv.org/abs/1234", reply_to_id=7))
    assert c.message_type == "url"
    assert c.is_reply
    assert c.reply_to_telegram_id == 7


# ---------------------------------------------------------------------------
# URL extraction
# ---------------------------------------------------------------------------


def test_extract_url_http() -> None:
    assert extract_url("Look at https://example.com please") == "https://example.com"


def test_extract_url_with_path() -> None:
    url = "https://arxiv.org/abs/2301.07041"
    assert extract_url(f"Paper: {url}") == url


def test_extract_url_strips_trailing_punctuation() -> None:
    assert extract_url("See https://example.com.") == "https://example.com"
    assert extract_url("(https://example.com)") == "https://example.com"


def test_extract_url_absent() -> None:
    assert extract_url("No links here, just text.") is None


def test_extract_url_empty_string() -> None:
    assert extract_url("") is None


# ---------------------------------------------------------------------------
# Channel assignment — deterministic keyword scoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_channel",
    [
        ("New paper on arxiv about LLM benchmark evaluation", "signal"),
        ("Just shipped the refactor, merged the pull request", "build"),
        ("Working on the thesis evaluation and ablation study", "research"),
        ("Preparing exercises for tomorrow's clase", "teaching"),
        ("Creo que the tradeoff here is perspective vs principle", "philosophy"),
        ("Random text with no matching keywords", None),
    ],
)
def test_channel_assignment(text: str, expected_channel: str | None) -> None:
    c = classify(_msg(text))
    assert c.channel == expected_channel


def test_channel_none_for_empty_text() -> None:
    c = classify(_msg(has_voice=True))  # voice with no text
    assert c.channel is None
