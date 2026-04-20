"""
Shared context kernel for Velveteen agents.

Combines:
- static brand/founder/output context from a checked-in markdown file
- small dynamic snapshots from SQLite

This is intentionally lightweight. It is retrieval, not a vector store.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import aiosqlite

from app.core.brand_voice import BRAND_VOICE
from app.core.config import settings
from app.db.queries import (
    get_recent_editorial_drafts,
    get_recent_editorial_plans,
    get_recent_signals,
)

_STATIC_CONTEXT_PATH = (
    Path(__file__).resolve().parent.parent / "context" / "velveteen_linkedin_github.md"
)


@lru_cache(maxsize=1)
def get_static_context() -> str:
    static_text = _STATIC_CONTEXT_PATH.read_text(encoding="utf-8").strip()
    tone_markers = ", ".join(BRAND_VOICE.tone_markers)
    anti_patterns = ", ".join(BRAND_VOICE.anti_patterns)
    priority_domains = ", ".join(BRAND_VOICE.priority_domains)
    return (
        f"{static_text}\n\n"
        "## Structured brand voice\n"
        f"Tone markers: {tone_markers}\n"
        f"Avoid: {anti_patterns}\n"
        f"Priority domains: {priority_domains}\n"
        f"Editorial rule: {BRAND_VOICE.editorial_rule}\n"
    )


def _compact(text: str | None, limit: int = 120) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


async def build_dynamic_context(db: aiosqlite.Connection) -> str:
    signals = await get_recent_signals(db, limit=3)
    plans = await get_recent_editorial_plans(db, limit=3)
    drafts = await get_recent_editorial_drafts(db, limit=2)

    signal_lines = []
    for row in signals:
        signal_lines.append(
            f"- signal #{row['id']} [{row['source_type']}] "
            f"{_compact(str(row['title'] or ''), 80)} "
            f"(score={float(row['relevance_score'] or 0.0):.2f})"
        )

    plan_lines = []
    for row in plans:
        signal_ids = json.loads(str(row["signal_ids"]))
        plan_lines.append(
            f"- plan #{row['id']} status={row['status']} "
            f"action={row['recommended_action']} signals={signal_ids}"
        )

    draft_lines = []
    for row in drafts:
        draft_lines.append(
            f"- draft #{row['id']} plan=#{row['plan_id']} status={row['status']}"
        )

    priority_repos = ", ".join(settings.priority_github_repo_list) or "none configured"

    sections = [
        "## Dynamic working context",
        f"Priority repos: {priority_repos}",
        "Recent signals:",
        *(signal_lines or ["- none"]),
        "Recent editorial plans:",
        *(plan_lines or ["- none"]),
        "Recent editorial drafts:",
        *(draft_lines or ["- none"]),
    ]
    return "\n".join(sections)
