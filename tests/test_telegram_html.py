"""
Every formatter output must be HTML Telegram accepts, and long messages must
survive chunking with balanced tags. A single unescaped `<x>` makes Telegram
return 400 and the user receives nothing, so this is a delivery test, not a
cosmetic one.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.integrations.telegram_client import _balance_html_chunks, _message_chunks
from app.schemas.linkedin import LinkedInPost, LinkedInPromptKit
from app.utils import telegram_formatting as fmt
from app.utils.text import trim_to_boundary

_ALLOWED_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "span",
    "tg-spoiler",
    "a",
    "code",
    "pre",
    "blockquote",
    "tg-emoji",
}
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)(?:\s[^>]*)?>")
_ENTITY_RE = re.compile(r"&(lt|gt|amp|quot|#\d+|#x[0-9a-fA-F]+);")


def assert_valid_telegram_html(text: str) -> None:
    stack: list[str] = []
    for match in _TAG_RE.finditer(text):
        closing, name = bool(match.group(1)), match.group(2).lower()
        assert name in _ALLOWED_TAGS, f"unsupported tag <{name}> in: {text[:200]}"
        if closing:
            assert stack and stack[-1] == name, f"unbalanced </{name}>"
            stack.pop()
        else:
            stack.append(name)
    assert not stack, f"unclosed tags {stack}"

    stripped = _TAG_RE.sub("", text)
    assert "<" not in stripped and ">" not in stripped, (
        f"raw angle bracket outside a tag in: {stripped[:200]}"
    )
    for amp in re.finditer("&", stripped):
        assert _ENTITY_RE.match(stripped, amp.start()), "bare & outside an entity"


def _post() -> LinkedInPost:
    return LinkedInPost(
        hook="Lo que me llama la atención de este paper es la arquitectura.",
        body_paragraphs=[
            "Primer párrafo con una idea concreta sobre el sistema.",
            "Segundo párrafo con la conexión a mi propio trabajo <con> brackets.",
        ],
        closing="¿Qué métrica usarías para auditar esta representación?",
        hashtags=["AppliedAI"],
    )


def test_linkedin_post_default_path_is_valid_html() -> None:
    text = fmt.format_linkedin_post(
        _post(),
        plan_id=7,
        llm_used=True,
        source_urls=[("Paper <A & B>", "https://arxiv.org/abs/1234.5678")],
        opinion_used=False,
    )
    assert_valid_telegram_html(text)
    assert "/opinion" in text


def test_linkedin_post_opinion_path_is_valid_html() -> None:
    text = fmt.format_linkedin_post(
        _post(), plan_id=7, llm_used=False, source_urls=None, opinion_used=True
    )
    assert_valid_telegram_html(text)


def test_linkedin_prompt_kit_survives_chunking() -> None:
    kit = LinkedInPromptKit(
        plan_id=3,
        system_prompt="Regla <uno> & dos.\n\n" + ("línea de contexto.\n" * 400),
        user_prompt="Plan id: 3\n\n" + ("señal de prueba.\n" * 120),
        one_line_paste_command="Eres mi asistente editorial. Devuélveme el post.",
    )
    text = fmt.format_linkedin_prompt_kit(kit)
    assert_valid_telegram_html(text)
    assert len(text) > 4096
    chunks = _balance_html_chunks(_message_chunks(text, limit=4096 - 64))
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 4096
        assert_valid_telegram_html(chunk)


def test_balance_reopens_nested_tags() -> None:
    chunks = _balance_html_chunks(["<b>bold <pre>code", "more code</pre> tail</b>"])
    assert chunks == [
        "<b>bold <pre>code</pre></b>",
        "<b><pre>more code</pre> tail</b>",
    ]


@pytest.mark.parametrize(
    "name",
    [n for n in dir(fmt) if n.startswith("format_")],
)
def test_zero_arg_formatters_are_valid_html(name: str) -> None:
    func = getattr(fmt, name)
    required = [
        p
        for p in inspect.signature(func).parameters.values()
        if p.default is inspect.Parameter.empty
    ]
    if required:
        pytest.skip("needs arguments")
    assert_valid_telegram_html(func())


def test_trim_to_boundary_never_uses_ellipsis() -> None:
    long = "Primera frase completa. Segunda frase que sigue " * 20
    out = trim_to_boundary(long, 120)
    assert len(out) <= 120
    assert "…" not in out and not out.endswith("...")
    assert out.endswith(".")
    assert trim_to_boundary("palabra " * 50, 40).endswith("palabra")
    assert trim_to_boundary("x" * 300, 50) == "x" * 50
    assert trim_to_boundary(None, 50) == ""
