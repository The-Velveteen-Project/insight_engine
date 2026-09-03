"""
Text trimming that never leaves a message hanging mid-word.

Hard rule for every operator output: no `…` truncation. If a field must be
shortened, cut at a sentence boundary when one is available, otherwise at a
word boundary, and let the caller point to the full source if needed.
"""

from __future__ import annotations


def trim_to_boundary(text: str | None, limit: int) -> str:
    """Collapse whitespace and cut `text` to at most `limit` chars.

    Preference order for the cut point:
    1. the last sentence end (". ", "! ", "? ") in the first half or later
    2. the last word boundary past a third of the window
    3. the raw window, only when the text is a single very long token
    """
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    if limit <= 0:
        return ""

    window = compact[:limit]
    sentence_cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if sentence_cut >= limit // 2:
        return window[: sentence_cut + 1]

    word_cut = window.rfind(" ")
    if word_cut >= limit // 3:
        return window[:word_cut].rstrip(" ,;:-")

    return window
