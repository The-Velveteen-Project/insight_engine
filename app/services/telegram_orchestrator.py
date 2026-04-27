"""
Telegram command orchestration layer for Phase 9.

This module acts as glue:
- parses Telegram commands
- invokes existing discovery and GitHub services
- uses editorial planning logic conservatively
- formats compact Telegram responses
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from json import JSONDecodeError, loads
from typing import Literal

import aiosqlite

from app.core.config import settings
from app.db.queries import (
    get_signal_by_source_identity,
    get_signals_by_ids,
    get_telegram_session,
    upsert_telegram_session,
)
from app.schemas.commands import (
    CommandName,
    MvpIdeaSuggestion,
    ParsedTelegramCommand,
    SignalSuggestion,
    WeeklySourceStats,
    WeeklySummary,
)
from app.schemas.discovery import SignalCandidate
from app.schemas.drafts import PersistedEditorialDraft
from app.schemas.editorial import (
    EditorialPlan,
    EditorialPlanStatus,
    EditorialSignalContext,
    PersistedEditorialPlan,
    RecommendedAction,
    WeeklyThesis,
    WeeklyThesisGenerationInput,
)
from app.schemas.github import GitHubInsightCandidate
from app.schemas.mvp_handoff import MvpHandoffPack
from app.services import (
    active_goals,
    discovery_service,
    draft_generator,
    editorial_planner,
    github_insight_service,
    handoff_followups,
    linkedin_writer,
    mvp_handoff,
)
from app.services.generation import get_weekly_thesis_generator
from app.utils import telegram_formatting

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(
    r"^/(?P<name>[A-Za-z_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(?P<query>.*))?$"
)
_QUERY_REQUIRED = {
    CommandName.PAPERS,
    CommandName.NEWS,
    CommandName.SIGNALS,
    CommandName.MVP_IDEAS,
}
_ID_REQUIRED = {
    CommandName.PLAN,
    CommandName.APPROVE,
    CommandName.DISCARD_PLAN,
    CommandName.DRAFT,
    CommandName.SHOW_PLAN,
    CommandName.SHOW_DRAFT,
    CommandName.MVP_HANDOFF,
    CommandName.LINKEDIN,
    CommandName.LINKEDIN_PROMPT,
}
_DISCOVERY_LABELS: dict[str, str] = {
    "arxiv": "arXiv API",
    "hackernews": "Hacker News Algolia",
    "github": "GitHub REST",
    "exa": "Exa Neural Search",
}
_LINKEDIN_PROMPT_RE = re.compile(
    r"^(?:dame|sacame|preparame)?\s*(?:el\s+)?prompt\s+"
    r"(?:de\s+|para\s+)?linked[Ii]n\s*"
    r"(?:del?\s+plan)?\s*(?P<target>\d+)?\s*$",
    re.I,
)
_LINKEDIN_POST_RE = re.compile(
    r"^(?:armame|preparame|sacame|dame|hazme)?\s*"
    r"(?:el\s+|un\s+)?post\s+(?:de\s+|para\s+)?linked[Ii]n\s*"
    r"(?:del?\s+plan)?\s*(?P<target>\d+)?\s*$",
    re.I,
)

_NATURAL_INTENTS: list[tuple[re.Pattern[str], CommandName]] = [
    (
        re.compile(r"^(?:help|ayuda|qué puedes hacer|que puedes hacer)\s*$", re.I),
        CommandName.HELP,
    ),
    (
        re.compile(r"^(?:papers?|busca papers? sobre)\s+(?P<query>.+)$", re.I),
        CommandName.PAPERS,
    ),
    (
        re.compile(r"^(?:news|noticias|busca noticias sobre)\s+(?P<query>.+)$", re.I),
        CommandName.NEWS,
    ),
    (
        re.compile(
            r"^(?:signals|señales|senales|busca señales sobre|"
            r"busca senales sobre)\s+(?P<query>.+)$",
            re.I,
        ),
        CommandName.SIGNALS,
    ),
    (
        re.compile(r"^(?:github[_ ]?insights|revisa mis repos)\s*$", re.I),
        CommandName.GITHUB_INSIGHTS,
    ),
    (
        re.compile(r"^(?:weekly|resumen semanal)\s*$", re.I),
        CommandName.WEEKLY,
    ),
    (
        re.compile(
            r"^(?:mvp[_ ]?ideas|ideas de mvp|que mvp sale de)\s+(?P<query>.+)$",
            re.I,
        ),
        CommandName.MVP_IDEAS,
    ),
    (
        re.compile(r"^(?:mvp[_ ]?handoff|prompt del mvp)\s+(?P<target>.+)$", re.I),
        CommandName.MVP_HANDOFF,
    ),
]
_GREETING_RE = re.compile(
    r"^(?:hola|hola!|buenas|buenos dias|buen día|buen dia|"
    r"buenas tardes|buenas noches|hey|hello)\s*$",
    re.I,
)
_GRATITUDE_RE = re.compile(
    r"^(?:gracias|muchas gracias|gracias!|perfecto|perfecto!|buenisimo|buenísimo)\s*$",
    re.I,
)
_POSTPONE_RE = re.compile(
    r"^(?:despues|después|luego|mas tarde|más tarde|ahora no|"
    r"todavia no|todavía no|aun no|aún no)\s*$",
    re.I,
)
_PREFER_DRAFT_RE = re.compile(
    r"^(?:no,?\s*mejor\s*draft|mejor\s*draft|"
    r"no,?\s*draft|saca(?:me)?\s+(?:el|un)?\s*draft|"
    r"no,?\s*hazme\s+(?:un\s+)?draft)\s*$",
    re.I,
)
_DISMISS_FOLLOWUP_RE = re.compile(
    r"^(?:olvidalo|olvídalo|olvida|ya esta|ya está|"
    r"dejalo|déjalo|cancelar|cancela|nada|nope)\s*$",
    re.I,
)
_GOAL_QUERY_RE = re.compile(
    r"^(?:cual es mi goal|cuál es mi goal|"
    r"muestrame mi goal|muéstrame mi goal|mi goal)\s*$",
    re.I,
)
_EXPLAIN_SIGNALS_RE = re.compile(
    r"(?:"
    r"podr[ií]as?\s+(?:explicar(?:me)?|contarme|detallar(?:me)?)"
    r"|explica(?:me)?\s+(?:mejor|mas|más|cada uno|los|estas?)"
    r"|cu[eé]ntame\s+(?:mas|más|mejor|sobre cada|de qu[eé])"
    r"|de\s+qu[eé]\s+(?:trata|van|se\s+trata)"
    r"|qu[eé]\s+(?:son|dice[ns]?|trata[ns]?)\s+(?:cada uno|estas?|los|las)"
    r"|mas\s+(?:detalle|info|contexto)"
    r"|m[aá]s\s+(?:detalle|info|contexto)"
    r"|detalla(?:me)?\s+(?:cada uno|los|las|estas?)"
    r")",
    re.I,
)
_GOAL_CLEAR_RE = re.compile(
    r"^(?:borra (?:mi |el )?goal|limpia (?:mi |el )?goal|"
    r"olvida (?:mi |el )?goal|quita (?:mi |el )?goal)\s*$",
    re.I,
)
_GOAL_SET_RE = re.compile(
    r"^(?:cambia(?:me)? (?:mi |el )?goal a |"
    r"renueva (?:mi |el )?goal a |"
    r"actualiza (?:mi |el )?goal a |"
    r"pon (?:mi |el )?goal en |"
    r"mi nuevo goal es )(?P<query>.+)$",
    re.I,
)
_FIRST_TOKENS: dict[str, CommandName] = {
    "start": CommandName.START,
    "help": CommandName.HELP,
    "papers": CommandName.PAPERS,
    "paper": CommandName.PAPERS,
    "news": CommandName.NEWS,
    "signals": CommandName.SIGNALS,
    "signal": CommandName.SIGNALS,
    "github_insights": CommandName.GITHUB_INSIGHTS,
    "weekly": CommandName.WEEKLY,
    "mvp_ideas": CommandName.MVP_IDEAS,
    "plan": CommandName.PLAN,
    "approve": CommandName.APPROVE,
    "discard_plan": CommandName.DISCARD_PLAN,
    "draft": CommandName.DRAFT,
    "show_plan": CommandName.SHOW_PLAN,
    "show_draft": CommandName.SHOW_DRAFT,
    "mvp_handoff": CommandName.MVP_HANDOFF,
    "goal": CommandName.GOAL,
    "clear_goal": CommandName.CLEAR_GOAL,
    "linkedin": CommandName.LINKEDIN,
    "linkedin_prompt": CommandName.LINKEDIN_PROMPT,
}
_TARGET_PATTERNS: list[tuple[re.Pattern[str], CommandName]] = [
    (
        re.compile(
            r"^(?:hazme|crea|make|create)?\s*(?:un\s+)?plan(?:\s+(?:del?|for)\s+)?(?P<target>.+)$",
            re.I,
        ),
        CommandName.PLAN,
    ),
    (
        re.compile(
            r"^(?:apruebalo|aprueba|approve)"
            r"(?:\s+(?:el\s+)?plan)?\s*(?P<target>.+)?$",
            re.I,
        ),
        CommandName.APPROVE,
    ),
    (
        re.compile(
            r"^(?:descartalo|descarta|discard)"
            r"(?:\s+(?:el\s+)?plan)?\s*(?P<target>.+)?$",
            re.I,
        ),
        CommandName.DISCARD_PLAN,
    ),
    (
        re.compile(
            r"^(?:hazme|crea|generate|sacame|dame)?\s*(?:un\s+)?draft(?:\s+(?:del?|for)\s+(?:plan\s+)?)?(?P<target>.+)?$",
            re.I,
        ),
        CommandName.DRAFT,
    ),
    (
        re.compile(
            r"^(?:muestrame|muéstrame|show)\s*(?:el\s+)?plan\s*(?P<target>.+)?$",
            re.I,
        ),
        CommandName.SHOW_PLAN,
    ),
    (
        re.compile(
            r"^(?:muestrame|muéstrame|show)\s*(?:el\s+)?draft\s*(?P<target>.+)?$",
            re.I,
        ),
        CommandName.SHOW_DRAFT,
    ),
]


@dataclass
class _ChatState:
    last_signal_ids: list[int]
    last_plan_id: int | None = None
    last_draft_id: int | None = None
    pending_command: CommandName | None = None
    pending_target_id: int | None = None


_CHAT_STATE: dict[int, _ChatState] = {}


def invalidate_cached_state(chat_id: int) -> None:
    """Drop the in-memory cache entry for a chat.

    Used by background workers (e.g. handoff follow-ups) after they update
    the persisted session row directly, so the next webhook re-reads from
    SQLite instead of a stale dict.
    """
    _CHAT_STATE.pop(chat_id, None)


@dataclass(frozen=True)
class _CandidateRef:
    source_type: Literal["arxiv", "hackernews", "github", "exa"]
    source_id: str
    title: str
    url: str
    summary: str
    relevance_score: float
    relevance_note: str


def parse_command(text: str) -> ParsedTelegramCommand:
    stripped = text.strip()
    match = _COMMAND_RE.match(stripped)
    if match is None:
        return ParsedTelegramCommand(
            name=CommandName.UNKNOWN,
            query=None,
            raw_text=text,
        )

    raw_name = match.group("name").lower()
    query = (match.group("query") or "").strip() or None
    try:
        name = CommandName(raw_name)
    except ValueError:
        name = CommandName.UNKNOWN

    return ParsedTelegramCommand(name=name, query=query, raw_text=text)


def is_command_text(text: str | None) -> bool:
    return bool(text and text.strip().startswith("/"))


def _usage(command_name: CommandName) -> str:
    examples = {
        CommandName.PLAN: "/plan <signal_id>",
        CommandName.APPROVE: "/approve <plan_id>",
        CommandName.DISCARD_PLAN: "/discard_plan <plan_id>",
        CommandName.DRAFT: "/draft <plan_id>",
        CommandName.SHOW_PLAN: "/show_plan <plan_id>",
        CommandName.SHOW_DRAFT: "/show_draft <draft_id>",
        CommandName.MVP_HANDOFF: "/mvp_handoff <plan_id>",
        CommandName.LINKEDIN: "/linkedin <plan_id>",
        CommandName.LINKEDIN_PROMPT: "/linkedin_prompt <plan_id>",
    }
    example = examples.get(command_name)
    if example is None:
        return telegram_formatting.format_help()
    return f"<b>Usage</b>\n<code>{telegram_formatting.escape_text(example)}</code>"


def _parse_positive_int(raw_value: str | None) -> int | None:
    if raw_value is None:
        return None
    try:
        value = int(raw_value.strip())
    except ValueError:
        return None
    return value if value > 0 else None


_GOAL_BY_RE = re.compile(r"\s+--by\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$", re.I)
_GOAL_BY_ATTEMPT_RE = re.compile(r"\s+--by\s+\S", re.I)


def _parse_goal_args(raw: str) -> tuple[str, datetime | None] | None:
    """Parse `<label>` or `<label> --by YYYY-MM-DD`. Returns None if invalid."""
    text = raw.strip()
    if not text:
        return None
    match = _GOAL_BY_RE.search(text)
    deadline: datetime | None = None
    if match is not None:
        try:
            parsed = datetime.strptime(match.group("date"), "%Y-%m-%d")
        except ValueError:
            return None
        deadline = parsed.replace(tzinfo=UTC)
        text = text[: match.start()].rstrip()
    elif _GOAL_BY_ATTEMPT_RE.search(text):
        # User tried `--by ...` but the date didn't parse — treat as invalid
        # rather than swallowing the whole tail into the label.
        return None
    label = text.strip().strip('"').strip("'")
    if len(label) < 4:
        return None
    return label, deadline


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _default_state() -> _ChatState:
    return _ChatState(last_signal_ids=[])


def _get_state(chat_id: int | None) -> _ChatState | None:
    if chat_id is None:
        return None
    return _CHAT_STATE.setdefault(chat_id, _default_state())


def _state_from_row(row: aiosqlite.Row) -> _ChatState:
    raw_signal_ids = row["last_signal_ids"] or "[]"
    try:
        parsed = loads(str(raw_signal_ids))
    except JSONDecodeError:
        parsed = []
    signal_ids = [int(item) for item in parsed if isinstance(item, int)]

    pending_raw = row["pending_command"]
    pending_command = None
    if isinstance(pending_raw, str):
        try:
            pending_command = CommandName(pending_raw)
        except ValueError:
            pending_command = None

    return _ChatState(
        last_signal_ids=signal_ids,
        last_plan_id=int(row["last_plan_id"])
        if row["last_plan_id"] is not None
        else None,
        last_draft_id=(
            int(row["last_draft_id"]) if row["last_draft_id"] is not None else None
        ),
        pending_command=pending_command,
        pending_target_id=(
            int(row["pending_target_id"])
            if row["pending_target_id"] is not None
            else None
        ),
    )


async def _load_state(
    db: aiosqlite.Connection,
    chat_id: int | None,
) -> _ChatState | None:
    if chat_id is None:
        return None
    cached = _CHAT_STATE.get(chat_id)
    if cached is not None:
        return cached

    row = await get_telegram_session(db, chat_id)
    state = _state_from_row(row) if row is not None else _default_state()
    _CHAT_STATE[chat_id] = state
    return state


async def _persist_state(
    db: aiosqlite.Connection,
    chat_id: int | None,
) -> None:
    if chat_id is None:
        return
    state = _CHAT_STATE.get(chat_id)
    if state is None:
        return
    await upsert_telegram_session(
        db,
        chat_id=chat_id,
        last_signal_ids=state.last_signal_ids,
        last_plan_id=state.last_plan_id,
        last_draft_id=state.last_draft_id,
        pending_command=(
            state.pending_command.value if state.pending_command is not None else None
        ),
        pending_target_id=state.pending_target_id,
    )


def _set_pending(
    chat_id: int | None,
    *,
    command_name: CommandName | None,
    target_id: int | None,
) -> None:
    state = _get_state(chat_id)
    if state is None:
        return
    state.pending_command = command_name
    state.pending_target_id = target_id


def _remember_signal_ids(chat_id: int | None, signal_ids: list[int]) -> None:
    state = _get_state(chat_id)
    if state is None:
        return
    state.last_signal_ids = signal_ids[: settings.telegram_command_limit]
    if state.last_signal_ids:
        state.pending_command = CommandName.PLAN
        state.pending_target_id = state.last_signal_ids[0]
    else:
        state.pending_command = None
        state.pending_target_id = None


def _remember_plan(chat_id: int | None, plan: PersistedEditorialPlan) -> None:
    state = _get_state(chat_id)
    if state is None:
        return
    state.last_plan_id = plan.plan_id
    state.last_signal_ids = plan.proposal.signal_ids
    state.last_draft_id = None
    if plan.status == EditorialPlanStatus.DRAFT:
        state.pending_command = CommandName.APPROVE
        state.pending_target_id = plan.plan_id
    elif plan.status == EditorialPlanStatus.APPROVED:
        # MVP plans get a proactive handoff offer attached to the approve
        # response, so the next "hazlo" must trigger /mvp_handoff, not /draft.
        if plan.proposal.recommended_action == RecommendedAction.MVP:
            state.pending_command = CommandName.MVP_HANDOFF
        else:
            state.pending_command = CommandName.DRAFT
        state.pending_target_id = plan.plan_id
    else:
        state.pending_command = None
        state.pending_target_id = None


def _remember_draft(chat_id: int | None, draft: PersistedEditorialDraft) -> None:
    state = _get_state(chat_id)
    if state is None:
        return
    state.last_draft_id = draft.draft_id
    state.last_plan_id = draft.plan_id
    state.last_signal_ids = draft.draft.signal_ids
    state.pending_command = None
    state.pending_target_id = None


def _resolve_signal_target(
    raw_target: str | None,
    state: _ChatState | None,
) -> int | None:
    if raw_target is None:
        return None
    target = raw_target.strip().lower().lstrip("#")
    if target in {"first", "primero"}:
        return state.last_signal_ids[0] if state and state.last_signal_ids else None
    if target in {"second", "segundo"}:
        if state and len(state.last_signal_ids) > 1:
            return state.last_signal_ids[1]
        return None
    if target in {"third", "tercero"}:
        if state and len(state.last_signal_ids) > 2:
            return state.last_signal_ids[2]
        return None
    return _parse_positive_int(target)


def _resolve_plan_target(
    raw_target: str | None,
    state: _ChatState | None,
) -> int | None:
    if raw_target is None or not raw_target.strip():
        return state.last_plan_id if state else None
    target = raw_target.strip().lower().lstrip("#")
    if target in {"it", "este", "ese", "ultimo", "último", "last"}:
        return state.last_plan_id if state else None
    return _parse_positive_int(target)


def _resolve_draft_target(
    raw_target: str | None,
    state: _ChatState | None,
) -> int | None:
    if raw_target is None or not raw_target.strip():
        return state.last_draft_id if state else None
    target = raw_target.strip().lower().lstrip("#")
    if target in {"it", "este", "ese", "ultimo", "último", "last"}:
        return state.last_draft_id if state else None
    return _parse_positive_int(target)


def _pending_to_command(state: _ChatState | None) -> ParsedTelegramCommand | None:
    if (
        state is None
        or state.pending_command is None
        or state.pending_target_id is None
    ):
        return None
    return ParsedTelegramCommand(
        name=state.pending_command,
        query=str(state.pending_target_id),
        raw_text=state.pending_command.value,
    )


def _pending_help(state: _ChatState | None) -> str:
    if (
        state is None
        or state.pending_command is None
        or state.pending_target_id is None
    ):
        return "\n".join(
            [
                "<b>Sin acción pendiente</b>",
                "Puedes probar con:",
                "• signals climate risk",
                "• github_insights",
                "• show_plan 12",
            ]
        )

    if state.pending_command == CommandName.PLAN:
        return (
            "<b>Siguiente paso</b>\n"
            f"Armar un plan desde la señal <code>#{state.pending_target_id}</code>.\n"
            "Puedes decir <code>hazlo</code> o <code>plan del primero</code>."
        )
    if state.pending_command == CommandName.APPROVE:
        return (
            "<b>Siguiente paso</b>\n"
            f"Aprobar el plan <code>#{state.pending_target_id}</code>.\n"
            "Puedes decir <code>hazlo</code> o <code>apruébalo</code>."
        )
    if state.pending_command == CommandName.DRAFT:
        return (
            "<b>Siguiente paso</b>\n"
            f"Sacar un draft del plan <code>#{state.pending_target_id}</code>.\n"
            "Puedes decir <code>hazlo</code> o <code>draft</code>."
        )
    return telegram_formatting.format_help()


def _continue_with_state(state: _ChatState | None) -> ParsedTelegramCommand | None:
    pending = _pending_to_command(state)
    if pending is not None:
        return pending
    if state is not None and state.last_draft_id is not None:
        return ParsedTelegramCommand(
            name=CommandName.SHOW_DRAFT,
            query=str(state.last_draft_id),
            raw_text="show_draft",
        )
    if state is not None and state.last_plan_id is not None:
        return ParsedTelegramCommand(
            name=CommandName.SHOW_PLAN,
            query=str(state.last_plan_id),
            raw_text="show_plan",
        )
    return None


def _natural_command(
    text: str,
    *,
    chat_id: int | None = None,
) -> ParsedTelegramCommand | None:
    stripped = text.strip()
    if not stripped:
        return None
    normalized = _strip_accents(stripped)
    lowered = normalized.lower()
    state = _get_state(chat_id)

    if _GREETING_RE.match(lowered):
        return ParsedTelegramCommand(
            name=CommandName.HELP,
            query="__greeting__",
            raw_text=text,
        )
    if _GRATITUDE_RE.match(lowered):
        return ParsedTelegramCommand(
            name=CommandName.HELP,
            query="__gratitude__",
            raw_text=text,
        )
    if _POSTPONE_RE.match(lowered):
        if (
            state is not None
            and state.pending_command == CommandName.MVP_HANDOFF
            and state.pending_target_id is not None
        ):
            return ParsedTelegramCommand(
                name=CommandName.HELP,
                query="__postpone_handoff__",
                raw_text=text,
            )
        # No pending handoff offer: let the message fall through to note capture.
    if _PREFER_DRAFT_RE.match(lowered):
        if (
            state is not None
            and state.pending_command == CommandName.MVP_HANDOFF
            and state.pending_target_id is not None
        ):
            return ParsedTelegramCommand(
                name=CommandName.DRAFT,
                query=str(state.pending_target_id),
                raw_text=text,
            )
    if _DISMISS_FOLLOWUP_RE.match(lowered):
        return ParsedTelegramCommand(
            name=CommandName.HELP,
            query="__dismiss_followup__",
            raw_text=text,
        )
    if _EXPLAIN_SIGNALS_RE.search(lowered):
        if state and state.last_signal_ids:
            return ParsedTelegramCommand(
                name=CommandName.EXPLAIN_SIGNALS,
                query=None,
                raw_text=text,
            )
    if _GOAL_QUERY_RE.match(lowered):
        return ParsedTelegramCommand(
            name=CommandName.GOAL,
            query=None,
            raw_text=text,
        )
    if _GOAL_CLEAR_RE.match(lowered):
        return ParsedTelegramCommand(
            name=CommandName.CLEAR_GOAL,
            query=None,
            raw_text=text,
        )
    goal_set_match = _GOAL_SET_RE.match(stripped)
    if goal_set_match is not None:
        return ParsedTelegramCommand(
            name=CommandName.GOAL,
            query=goal_set_match.group("query").strip(),
            raw_text=text,
        )

    # `prompt linkedin ...` must match before the post pattern, otherwise
    # "post" rules would never be reached for this phrasing.
    linkedin_prompt_match = _LINKEDIN_PROMPT_RE.match(normalized)
    if linkedin_prompt_match is not None:
        target = linkedin_prompt_match.group("target")
        resolved = (
            _resolve_plan_target(target, state)
            if target
            else (state.last_plan_id if state else None)
        )
        if resolved is None:
            return ParsedTelegramCommand(
                name=CommandName.LINKEDIN_PROMPT,
                query=None,
                raw_text=text,
            )
        return ParsedTelegramCommand(
            name=CommandName.LINKEDIN_PROMPT,
            query=str(resolved),
            raw_text=text,
        )

    linkedin_post_match = _LINKEDIN_POST_RE.match(normalized)
    if linkedin_post_match is not None:
        target = linkedin_post_match.group("target")
        resolved = (
            _resolve_plan_target(target, state)
            if target
            else (state.last_plan_id if state else None)
        )
        if resolved is None:
            return ParsedTelegramCommand(
                name=CommandName.LINKEDIN,
                query=None,
                raw_text=text,
            )
        return ParsedTelegramCommand(
            name=CommandName.LINKEDIN,
            query=str(resolved),
            raw_text=text,
        )

    if lowered in {"hazlo", "dale", "continua", "continúa", "ok", "si", "sí"}:
        pending = _continue_with_state(state)
        if pending is not None:
            return pending
        return ParsedTelegramCommand(
            name=CommandName.HELP,
            query="__pending__",
            raw_text=text,
        )

    if lowered in {
        "sigamos con eso",
        "sigamos",
        "continuemos",
        "dale con eso",
        "sigue",
    }:
        follow_up = _continue_with_state(state)
        if follow_up is not None:
            return follow_up
        return ParsedTelegramCommand(
            name=CommandName.HELP,
            query="__pending__",
            raw_text=text,
        )

    if lowered in {"que sigue", "qué sigue", "que recomiendas", "qué recomiendas"}:
        return ParsedTelegramCommand(
            name=CommandName.HELP,
            query="__pending__",
            raw_text=text,
        )

    if lowered in {
        "dame una version corta",
        "dame una versión corta",
        "version corta",
        "versión corta",
        "resumen corto",
        "short version",
    }:
        if state and state.last_draft_id is not None:
            return ParsedTelegramCommand(
                name=CommandName.HELP,
                query="__short_draft__",
                raw_text=text,
            )
        return ParsedTelegramCommand(
            name=CommandName.HELP,
            query="__pending__",
            raw_text=text,
        )

    if lowered in {"ultimo draft", "último draft", "last draft"}:
        if state and state.last_draft_id is not None:
            return ParsedTelegramCommand(
                name=CommandName.SHOW_DRAFT,
                query=str(state.last_draft_id),
                raw_text=text,
            )

    if lowered in {"ultimo plan", "último plan", "last plan"}:
        if state and state.last_plan_id is not None:
            return ParsedTelegramCommand(
                name=CommandName.SHOW_PLAN,
                query=str(state.last_plan_id),
                raw_text=text,
            )

    if lowered in {"muestramelo", "muéstramelo", "show it"}:
        if state and state.last_draft_id is not None:
            return ParsedTelegramCommand(
                name=CommandName.SHOW_DRAFT,
                query=str(state.last_draft_id),
                raw_text=text,
            )
        if state and state.last_plan_id is not None:
            return ParsedTelegramCommand(
                name=CommandName.SHOW_PLAN,
                query=str(state.last_plan_id),
                raw_text=text,
            )
        return ParsedTelegramCommand(
            name=CommandName.HELP,
            query="__pending__",
            raw_text=text,
        )

    parts = stripped.split(maxsplit=1)
    first_token = _strip_accents(parts[0]).lower()
    command_name = _FIRST_TOKENS.get(first_token)
    if command_name is not None:
        remainder = parts[1] if len(parts) > 1 else None
        return ParsedTelegramCommand(name=command_name, query=remainder, raw_text=text)

    for pattern, command_name in _NATURAL_INTENTS:
        match = pattern.match(normalized)
        if match is None:
            continue
        query = match.groupdict().get("query") or match.groupdict().get("target")
        return ParsedTelegramCommand(
            name=command_name,
            query=query.strip() if query else None,
            raw_text=text,
        )

    for pattern, command_name in _TARGET_PATTERNS:
        match = pattern.match(normalized)
        if match is None:
            continue
        target = match.groupdict().get("target")
        if command_name == CommandName.PLAN:
            resolved = _resolve_signal_target(target, state)
        elif command_name in {
            CommandName.APPROVE,
            CommandName.DISCARD_PLAN,
            CommandName.DRAFT,
            CommandName.MVP_HANDOFF,
            CommandName.SHOW_PLAN,
        }:
            resolved = _resolve_plan_target(target, state)
        else:
            resolved = _resolve_draft_target(target, state)
        if resolved is None:
            return ParsedTelegramCommand(
                name=command_name,
                query=None,
                raw_text=text,
            )
        return ParsedTelegramCommand(
            name=command_name,
            query=str(resolved),
            raw_text=text,
        )

    return None


def _not_found(entity: str, entity_id: int) -> str:
    return f"<b>{entity} not found</b>\n<code>{entity_id}</code>"


def _invalid_transition(message: str) -> str:
    return f"<b>Invalid state</b>\n{telegram_formatting.escape_text(message)}"


def _candidate_ref(
    candidate: SignalCandidate | GitHubInsightCandidate,
) -> _CandidateRef:
    if isinstance(candidate, SignalCandidate):
        return _CandidateRef(
            source_type=candidate.source_type,
            source_id=candidate.source_id,
            title=candidate.title,
            url=str(candidate.url),
            summary=candidate.summary,
            relevance_score=candidate.relevance_score,
            relevance_note=candidate.relevance_note,
        )
    return _CandidateRef(
        source_type="github",
        source_id=candidate.source_id,
        title=candidate.title,
        url=candidate.url,
        summary=candidate.summary,
        relevance_score=candidate.relevance_score,
        relevance_note=candidate.relevance_note,
    )


async def _get_signal_id(
    db: aiosqlite.Connection,
    candidate: _CandidateRef,
) -> int | None:
    row = await get_signal_by_source_identity(
        db,
        source_type=candidate.source_type,
        source_id=candidate.source_id,
    )
    if row is None:
        return None
    return int(row["id"])


async def _plan_for_signal_ids(
    db: aiosqlite.Connection,
    signal_ids: list[int],
) -> EditorialPlan:
    # Use the LLM-backed planner whenever an OpenAI key is configured so the
    # weekly's "Mi lectura" gets a real editorial reading instead of the
    # generic deterministic angle. Falls back automatically when the key is
    # absent or the call fails.
    use_llm = bool(settings.openai_api_key.strip())
    return await editorial_planner.plan_editorial(
        db,
        signal_ids,
        use_generation=use_llm,
    )


async def _candidate_to_suggestion(
    db: aiosqlite.Connection,
    candidate: _CandidateRef,
) -> SignalSuggestion:
    signal_id = await _get_signal_id(db, candidate)
    if signal_id is None:
        suggested_action = RecommendedAction.NOTE
        why_it_matters = candidate.relevance_note or candidate.summary
    else:
        plan = await _plan_for_signal_ids(db, [signal_id])
        suggested_action = plan.recommended_action
        why_it_matters = plan.why_it_matters
    return SignalSuggestion(
        signal_id=signal_id,
        title=candidate.title,
        why_it_matters=why_it_matters,
        suggested_action=suggested_action,
        relevance_score=candidate.relevance_score,
        source_label=_DISCOVERY_LABELS[candidate.source_type],
        url=candidate.url,
    )


async def _candidates_to_suggestions(
    db: aiosqlite.Connection,
    candidates: list[_CandidateRef],
) -> list[SignalSuggestion]:
    suggestions: list[SignalSuggestion] = []
    for candidate in candidates[: settings.telegram_command_limit]:
        suggestions.append(await _candidate_to_suggestion(db, candidate))
    return suggestions


def _suggestion_signal_ids(suggestions: list[SignalSuggestion]) -> list[int]:
    return [
        suggestion.signal_id
        for suggestion in suggestions
        if suggestion.signal_id is not None
    ]


async def _discover_refs(
    db: aiosqlite.Connection,
    query: str,
    *,
    source_names: tuple[str, ...] | None = None,
    message_id: int | None = None,
) -> tuple[list[_CandidateRef], str]:
    """Returns (candidate_refs, normalized_query)."""
    sources = (
        discovery_service.get_sources_by_name(source_names)
        if source_names is not None
        else None
    )
    result = await discovery_service.discover(
        query,
        db,
        limit=settings.telegram_command_limit,
        message_id=message_id,
        sources=sources,
    )
    return [_candidate_ref(c) for c in result.signals], result.normalized_query


async def _discover_with_outcomes(
    db: aiosqlite.Connection,
    query: str,
    *,
    message_id: int | None = None,
) -> tuple[
    list[_CandidateRef],
    str,
    list[discovery_service.DiscoverySourceOutcome],
]:
    """Discover variant that also exposes per-source outcomes for the weekly."""
    result = await discovery_service.discover(
        query,
        db,
        limit=settings.telegram_command_limit,
        message_id=message_id,
        sources=None,
    )
    refs = [_candidate_ref(c) for c in result.signals]
    return refs, result.normalized_query, result.outcomes


async def _github_refs(
    db: aiosqlite.Connection,
    *,
    message_id: int | None = None,
) -> list[_CandidateRef]:
    repos = settings.priority_github_repo_list
    if not repos:
        return []
    candidates = await github_insight_service.suggest_repo_insights(
        repos,
        db,
        limit=settings.telegram_command_limit,
        message_id=message_id,
    )
    return [_candidate_ref(candidate) for candidate in candidates]


async def _github_refs_with_outcome(
    db: aiosqlite.Connection,
    *,
    message_id: int | None = None,
) -> tuple[list[_CandidateRef], discovery_service.DiscoverySourceOutcome | None]:
    """github_refs variant that also returns a single 'github' outcome row.

    Per-repo failures are still logged inside github_insight_service; this
    surfaces only the aggregate count for the weekly footer.
    """
    repos = settings.priority_github_repo_list
    if not repos:
        return [], None
    try:
        candidates = await github_insight_service.suggest_repo_insights(
            repos,
            db,
            limit=settings.telegram_command_limit,
            message_id=message_id,
        )
    except Exception as exc:  # belt-and-suspenders
        logger.warning("github_insight_service raised at weekly time: %s", exc)
        return [], discovery_service.DiscoverySourceOutcome(
            source_name="github",
            fetched=0,
            failed=True,
            error_summary=str(exc)[:200] or exc.__class__.__name__,
        )
    refs = [_candidate_ref(candidate) for candidate in candidates]
    return refs, discovery_service.DiscoverySourceOutcome(
        source_name="github",
        fetched=len(candidates),
    )


def _dedup_signal_ids(signal_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    ordered: list[int] = []
    for signal_id in signal_ids:
        if signal_id in seen:
            continue
        seen.add(signal_id)
        ordered.append(signal_id)
    return ordered


async def _top_signal_ids(
    db: aiosqlite.Connection,
    candidates: list[_CandidateRef],
    *,
    limit: int = 3,
) -> list[int]:
    pairs: list[tuple[float, int]] = []
    for candidate in candidates:
        signal_id = await _get_signal_id(db, candidate)
        if signal_id is None:
            continue
        pairs.append((candidate.relevance_score, signal_id))
    pairs.sort(key=lambda item: item[0], reverse=True)
    return _dedup_signal_ids([signal_id for _, signal_id in pairs])[:limit]


def _possible_sources(candidates: list[_CandidateRef]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        label = _DISCOVERY_LABELS[candidate.source_type]
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels or ["Persisted signals"]


def _system_type(candidates: list[_CandidateRef], action: RecommendedAction) -> str:
    source_types = {candidate.source_type for candidate in candidates}
    if action != RecommendedAction.MVP:
        return "editorial and signal review workflow"
    if "github" in source_types and len(source_types) > 1:
        return "small signal-to-build workflow with repo context"
    if "github" in source_types:
        return "repo insight and portfolio update workflow"
    return "small discovery and ranking workflow"


def _portfolio_fit(candidates: list[_CandidateRef], action: RecommendedAction) -> str:
    if action == RecommendedAction.MVP:
        return (
            "Fits the lab by turning external signals and repo work into a small, "
            "applied decision-systems build."
        )
    return (
        "Fits the lab by converting signals into sober editorial or research output "
        "without forcing an oversized build."
    )


def _next_step(plan: EditorialPlan, signal_ids: list[int]) -> str:
    signal_ref = ", ".join(f"#{signal_id}" for signal_id in signal_ids)
    if plan.recommended_action == RecommendedAction.MVP:
        return (
            f"Promote {signal_ref} into an approved plan and scope one one-week build."
        )
    if plan.recommended_action == RecommendedAction.NOTE:
        return f"Turn {signal_ref} into a technical note and keep the angle narrow."
    if plan.recommended_action == RecommendedAction.POST:
        return (
            f"Draft a compact public insight from {signal_ref} "
            "and keep tracking evidence."
        )
    return f"Archive {signal_ref} for now and wait for stronger convergence."


def _row_to_signal_context(row: aiosqlite.Row) -> EditorialSignalContext:
    return EditorialSignalContext(
        id=int(row["id"]),
        source_type=str(row["source_type"]),
        source_id=str(row["source_id"]) if row["source_id"] is not None else None,
        title=str(row["title"] or ""),
        summary=str(row["summary"] or ""),
        url=str(row["url"]) if row["url"] is not None else None,
        relevance_score=float(row["relevance_score"] or 0.0),
        relevance_note=str(row["relevance_note"] or ""),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
    )


def _deterministic_weekly_thesis(
    signal_contexts: list[EditorialSignalContext],
    plan: EditorialPlan,
    *,
    focus: str,
    focus_label: str | None,
    active_goal: str | None,
) -> WeeklyThesis:
    sources = {signal.source_type for signal in signal_contexts}
    has_own_repo = "github" in sources
    has_external = bool({"arxiv", "hackernews"} & sources)
    is_mvp = plan.recommended_action == RecommendedAction.MVP

    own_repo_names = sorted(
        {
            signal.title.split(":")[0].strip()
            for signal in signal_contexts
            if signal.source_type == "github" and signal.title
        }
    )
    external_titles = [
        signal.title for signal in signal_contexts if signal.source_type != "github"
    ]

    if has_own_repo and has_external:
        external_hint = external_titles[0] if external_titles else "una pieza externa"
        repos_hint = ", ".join(own_repo_names) if own_repo_names else "tus repos"
        opener = (
            f"Esta semana hay convergencia real: lo que mueves en {repos_hint} "
            f"se cruza con material externo (\"{external_hint}\") sobre {focus}. "
            "Vale la pena tratarlos como un solo movimiento editorial, no como "
            "tres signals sueltas."
        )
        strong = True
    elif has_external and not has_own_repo:
        ext_count = len(external_titles)
        opener = (
            f"Esta semana las señales fuertes vienen de afuera ({ext_count} "
            f"piezas alrededor de {focus}). No veo todavía actividad propia que "
            "las ancle, así que conviene leerlas antes de comprometerte a build."
        )
        strong = True
    elif has_own_repo and not has_external:
        repos_hint = ", ".join(own_repo_names) if own_repo_names else "tu repo"
        opener = (
            f"Esta semana el brief lo carga tu trabajo propio ({repos_hint}); "
            "no hay material externo defendible que lo acompañe. Es buen "
            "momento para apuntalar la estructura antes de buscar tracción "
            "afuera, o para sumar lectura externa sobre el mismo eje."
        )
        strong = False
    else:
        opener = (
            "Esta semana las señales son útiles pero sueltas: no veo una "
            f"tesis fuerte que las una más allá del foco general ({focus})."
        )
        strong = False

    if active_goal:
        opener += (
            f" Para tu objetivo activo ({active_goal}), revísalas con la "
            "pregunta de si te acercan al deadline o son ruido interesante."
        )

    handoff_reason: str | None = None
    if is_mvp and has_own_repo and has_external:
        handoff_reason = (
            "El paper externo más tu repo dan sustancia para scopear un build "
            "de una semana sobre el mismo eje, sin forzar el alcance."
        )
    elif is_mvp:
        handoff_reason = (
            "La línea editorial propuesta tiene tracción de MVP. Vale la pena "
            "decidir si lo armas ahora o lo dejas como nota."
        )

    if focus_label:
        opener = f"Sub-foco de la semana: {focus_label}. " + opener

    return WeeklyThesis(
        opening_paragraph=opener,
        has_strong_thesis=strong,
        suggests_handoff=is_mvp,
        handoff_reason=handoff_reason,
    )


async def _resolve_weekly_thesis(
    signal_contexts: list[EditorialSignalContext],
    plan: EditorialPlan,
    *,
    focus: str,
    focus_label: str | None,
    active_goal: str | None,
) -> tuple[WeeklyThesis, bool]:
    """Returns (thesis, llm_used). Falls back to deterministic on any failure."""
    generator = get_weekly_thesis_generator()
    if generator is not None:
        try:
            generated = await generator.generate(
                WeeklyThesisGenerationInput(
                    weekly_focus=focus,
                    focus_label=focus_label or None,
                    active_goal=active_goal or None,
                    chosen_action=plan.recommended_action,
                    chosen_angle=plan.angle,
                    signals=signal_contexts,
                )
            )
        except Exception as exc:  # belt-and-suspenders
            logger.warning("Weekly thesis generation raised: %s", exc)
            generated = None
        if generated is not None:
            return generated, True
    return (
        _deterministic_weekly_thesis(
            signal_contexts,
            plan,
            focus=focus,
            focus_label=focus_label,
            active_goal=active_goal,
        ),
        False,
    )


def _balanced_weekly_selection(
    external: list[_CandidateRef],
    github_refs: list[_CandidateRef],
    *,
    target: int = 3,
) -> list[_CandidateRef]:
    """Pick `target` candidates ensuring source diversity when possible.

    Rule: if both external and github have anything to offer, the brief
    reserves at least one slot for the top external and one slot for the
    top github (regardless of pure score). Remaining slots are filled by
    overall score across what's left. This stops github from drowning out
    external work just because his own repos accumulate artifact bonuses.
    """
    sorted_external = sorted(
        external, key=lambda c: c.relevance_score, reverse=True
    )
    sorted_github = sorted(
        github_refs, key=lambda c: c.relevance_score, reverse=True
    )
    selected: list[_CandidateRef] = []
    if sorted_external:
        selected.append(sorted_external[0])
    if sorted_github:
        selected.append(sorted_github[0])
    # Now fill the remaining slots from whichever pool has more, top-by-score.
    chosen_ids = {(c.source_type, c.source_id) for c in selected}
    remainder = [
        c
        for c in (*sorted_external, *sorted_github)
        if (c.source_type, c.source_id) not in chosen_ids
    ]
    remainder.sort(key=lambda c: c.relevance_score, reverse=True)
    selected.extend(remainder[: max(0, target - len(selected))])
    # Final ordering = score descending, so the top-scoring item leads the brief.
    return sorted(selected, key=lambda c: c.relevance_score, reverse=True)[:target]


def _build_weekly_source_stats(
    selected: list[_CandidateRef],
    discovery_outcomes: list[discovery_service.DiscoverySourceOutcome],
    github_outcome: discovery_service.DiscoverySourceOutcome | None,
) -> list[WeeklySourceStats]:
    """One row per source actually attempted, with how many made the brief."""
    in_brief_by_source: dict[str, int] = {}
    for candidate in selected:
        in_brief_by_source[candidate.source_type] = (
            in_brief_by_source.get(candidate.source_type, 0) + 1
        )
    stats: list[WeeklySourceStats] = []
    for outcome in discovery_outcomes:
        in_brief = in_brief_by_source.get(outcome.source_name, 0)
        note: str | None = None
        if outcome.failed:
            note = (
                f"falla en la fuente: {outcome.error_summary or 'sin detalle'}"
            )
        elif outcome.fetched > 0 and in_brief == 0:
            note = "ninguno superó la barra editorial"
        label = _DISCOVERY_LABELS.get(outcome.source_name, outcome.source_name)
        stats.append(
            WeeklySourceStats(
                source_label=label,
                candidates_returned=outcome.fetched,
                candidates_in_brief=in_brief,
                failed=outcome.failed,
                note=note,
            )
        )
    if github_outcome is not None:
        in_brief = in_brief_by_source.get("github", 0)
        note = None
        if github_outcome.failed:
            note = (
                f"falla en la fuente: {github_outcome.error_summary or 'sin detalle'}"
            )
        elif github_outcome.fetched > 0 and in_brief == 0:
            note = "ninguno superó la barra editorial"
        stats.append(
            WeeklySourceStats(
                source_label=_DISCOVERY_LABELS["github"],
                candidates_returned=github_outcome.fetched,
                candidates_in_brief=in_brief,
                failed=github_outcome.failed,
                note=note,
            )
        )
    return stats


async def build_weekly_summary(
    db: aiosqlite.Connection,
    *,
    query: str | None = None,
    message_id: int | None = None,
) -> WeeklySummary | None:
    resolved_query = query or settings.weekly_discovery_query
    external, _, discovery_outcomes = await _discover_with_outcomes(
        db, resolved_query, message_id=message_id
    )
    github_refs, github_outcome = await _github_refs_with_outcome(
        db, message_id=message_id
    )
    selected = _balanced_weekly_selection(external, github_refs, target=3)
    signals_evaluated = len(external) + len(github_refs)
    if not selected:
        return None

    signal_ids = await _top_signal_ids(db, selected, limit=3)
    if not signal_ids:
        return None

    suggestions = await _candidates_to_suggestions(db, selected[:3])
    plan = await _plan_for_signal_ids(db, signal_ids)
    mvp_idea = await build_mvp_idea(
        db,
        resolved_query,
        message_id=message_id,
        candidate_refs=sorted(
            external + github_refs,
            key=lambda c: c.relevance_score,
            reverse=True,
        ),
    )

    signal_rows = await get_signals_by_ids(db, signal_ids)
    signal_contexts = [_row_to_signal_context(row) for row in signal_rows]
    source_stats = _build_weekly_source_stats(
        selected, discovery_outcomes, github_outcome
    )

    focus_label = settings.weekly_focus_label.strip() or None

    current_goal = await active_goals.get_current(db)
    active_goal_text: str | None = None
    if current_goal is not None:
        deadline_summary = telegram_formatting.goal_deadline_summary(current_goal)
        active_goal_text = (
            f"{current_goal.label} · {deadline_summary}"
            if deadline_summary
            else current_goal.label
        )

    thesis: WeeklyThesis | None = None
    llm_used = False
    if signal_contexts:
        thesis, llm_used = await _resolve_weekly_thesis(
            signal_contexts,
            plan,
            focus=resolved_query,
            focus_label=focus_label,
            active_goal=active_goal_text,
        )

    handoff_proposal: str | None = None
    if thesis is not None and thesis.suggests_handoff and thesis.handoff_reason:
        handoff_proposal = thesis.handoff_reason

    return WeeklySummary(
        query=resolved_query,
        top_signals=suggestions[:3],
        editorial_action=plan.recommended_action,
        editorial_angle=plan.angle,
        mvp_action=mvp_idea.recommended_action,
        mvp_summary=mvp_idea.thesis,
        next_step=_next_step(plan, signal_ids),
        thesis_paragraph=thesis.opening_paragraph if thesis is not None else None,
        handoff_proposal=handoff_proposal,
        signals_evaluated=signals_evaluated,
        focus_label=focus_label,
        active_goal=active_goal_text,
        llm_thesis_used=llm_used,
        source_stats=source_stats,
    )


async def build_mvp_idea(
    db: aiosqlite.Connection,
    query: str,
    *,
    message_id: int | None = None,
    candidate_refs: list[_CandidateRef] | None = None,
) -> MvpIdeaSuggestion:
    combined = candidate_refs
    if combined is None:
        external, _ = await _discover_refs(db, query, message_id=message_id)
        github_refs = await _github_refs(db, message_id=message_id)
        combined = sorted(
            external + github_refs,
            key=lambda item: item.relevance_score,
            reverse=True,
        )

    if not combined:
        return MvpIdeaSuggestion(
            query=query,
            recommended_action=RecommendedAction.ARCHIVE,
            thesis="No useful signal base was found for an MVP suggestion.",
            problem="The current search did not produce enough relevant evidence.",
            why_it_matters=(
                "Forcing an MVP here would add noise rather than portfolio value."
            ),
            possible_sources=["Persisted signals"],
            system_type="no build suggested",
            portfolio_fit=(
                "Better to wait for stronger evidence before proposing a build."
            ),
            signal_ids=[],
            supporting_signals=[],
        )

    signal_ids = await _top_signal_ids(db, combined, limit=3)
    if not signal_ids:
        return MvpIdeaSuggestion(
            query=query,
            recommended_action=RecommendedAction.ARCHIVE,
            thesis="Signals were discovered, but they were not stable enough to use.",
            problem=(
                "The discovered candidates could not be mapped cleanly into "
                "persisted workflow ids."
            ),
            why_it_matters=(
                "A conservative fallback is better than inventing an unsupported MVP."
            ),
            possible_sources=_possible_sources(combined),
            system_type="no build suggested",
            portfolio_fit="Wait for a cleaner set of persisted signals.",
            signal_ids=[],
            supporting_signals=[],
        )

    supporting_signals = await _candidates_to_suggestions(db, combined[:3])
    plan = await _plan_for_signal_ids(db, signal_ids)
    if plan.recommended_action != RecommendedAction.MVP:
        return MvpIdeaSuggestion(
            query=query,
            recommended_action=plan.recommended_action,
            thesis=plan.angle,
            problem=(
                "The current evidence is useful, but it is not strong enough "
                "for a credible MVP."
            ),
            why_it_matters=plan.why_it_matters,
            possible_sources=_possible_sources(combined),
            system_type="editorial and signal review workflow",
            portfolio_fit=_portfolio_fit(combined, plan.recommended_action),
            signal_ids=signal_ids,
            supporting_signals=supporting_signals[:3],
        )

    return MvpIdeaSuggestion(
        query=query,
        recommended_action=RecommendedAction.MVP,
        thesis=plan.angle,
        problem=plan.why_it_matters,
        why_it_matters=(
            "The signals converge enough to justify a small, testable build "
            "rather than "
            "another commentary-only artifact."
        ),
        possible_sources=_possible_sources(combined),
        system_type=_system_type(combined, plan.recommended_action),
        portfolio_fit=_portfolio_fit(combined, plan.recommended_action),
        signal_ids=signal_ids,
        supporting_signals=supporting_signals[:3],
    )


def _query_heading(label: str, raw_query: str, normalized_query: str) -> str:
    """Build a heading centered on the founder's original query."""
    rq = raw_query.strip()
    return f"{label} · {rq}"


async def _format_query_results(
    db: aiosqlite.Connection,
    *,
    heading: str,
    candidates: list[_CandidateRef],
    normalized_query: str = "",
) -> str:
    if not candidates:
        return telegram_formatting.format_no_signals(heading, normalized_query)
    suggestions = await _candidates_to_suggestions(db, candidates)
    return telegram_formatting.format_signal_suggestions(
        heading, suggestions, normalized_query=normalized_query
    )


def _format_plan_created(plan: PersistedEditorialPlan) -> str:
    return telegram_formatting.format_plan_summary(
        plan,
        heading=f"Plan #{plan.plan_id} created",
    )


def _format_plan_updated(
    plan: PersistedEditorialPlan,
    *,
    verb: str,
) -> str:
    return telegram_formatting.format_plan_summary(
        plan,
        heading=f"Plan #{plan.plan_id} {verb}",
    )


def _format_draft_created(draft: PersistedEditorialDraft) -> str:
    return telegram_formatting.format_draft_summary(
        draft,
        heading=f"Draft #{draft.draft_id} created",
    )


def _format_mvp_handoff(pack: MvpHandoffPack) -> str:
    return telegram_formatting.format_mvp_handoff_summary(pack)


async def handle_command(
    raw_text: str,
    db: aiosqlite.Connection,
    *,
    message_id: int | None = None,
    chat_id: int | None = None,
) -> str:
    state = await _load_state(db, chat_id)
    command = parse_command(raw_text)

    if command.name == CommandName.START:
        return telegram_formatting.format_start_message()

    if command.name == CommandName.HELP or command.name == CommandName.UNKNOWN:
        if command.name == CommandName.HELP and command.query == "__greeting__":
            return telegram_formatting.format_greeting()
        if command.name == CommandName.HELP and command.query == "__gratitude__":
            return telegram_formatting.format_gratitude()
        if command.name == CommandName.HELP and command.query == "__short_draft__":
            draft_id = state.last_draft_id if state is not None else None
            if draft_id is None:
                return _pending_help(state)
            draft = await draft_generator.get_persisted_editorial_draft(db, draft_id)
            _remember_draft(chat_id, draft)
            return telegram_formatting.format_draft_short_version(draft)
        if command.name == CommandName.HELP and command.query == "__pending__":
            return _pending_help(state)
        if command.name == CommandName.HELP and command.query == "__postpone_handoff__":
            target_plan_id = state.pending_target_id if state is not None else None
            if target_plan_id is None or chat_id is None:
                return _pending_help(state)
            await handoff_followups.schedule_after_postpone(
                db,
                plan_id=target_plan_id,
                chat_id=chat_id,
            )
            _set_pending(chat_id, command_name=None, target_id=None)
            await _persist_state(db, chat_id)
            return telegram_formatting.format_handoff_postponed(target_plan_id)
        if command.name == CommandName.HELP and command.query == "__dismiss_followup__":
            if chat_id is None:
                return telegram_formatting.format_no_pending_followup()
            dismissed = await handoff_followups.dismiss_latest_for_chat(db, chat_id)
            if not dismissed:
                return telegram_formatting.format_no_pending_followup()
            _set_pending(chat_id, command_name=None, target_id=None)
            await _persist_state(db, chat_id)
            return telegram_formatting.format_followup_dismissed()
        if command.name == CommandName.UNKNOWN:
            return telegram_formatting.format_soft_unknown(command.raw_text)
        return telegram_formatting.format_help()

    if command.name == CommandName.GOAL:
        if command.query is None:
            current = await active_goals.get_current(db)
            if current is None:
                return telegram_formatting.format_no_active_goal()
            return telegram_formatting.format_active_goal(current)
        parsed_goal = _parse_goal_args(command.query)
        if parsed_goal is None:
            return (
                "<b>Usage</b>\n"
                "<code>/goal &quot;tu objetivo&quot; --by YYYY-MM-DD</code>\n"
                "El <code>--by</code> es opcional."
            )
        label, deadline = parsed_goal
        new_goal = await active_goals.set_active(
            db,
            label=label,
            deadline_at=deadline,
        )
        return telegram_formatting.format_goal_set(new_goal)

    if command.name == CommandName.CLEAR_GOAL:
        archived = await active_goals.clear_active(db)
        if archived is None:
            return telegram_formatting.format_no_active_goal()
        return telegram_formatting.format_goal_cleared(archived)

    if command.name == CommandName.EXPLAIN_SIGNALS:
        state = _get_state(chat_id)
        signal_ids = state.last_signal_ids if state else []
        if not signal_ids:
            return (
                "No tengo señales recientes en contexto. "
                "Corre <code>/weekly</code> primero para cargarlas."
            )
        rows = await get_signals_by_ids(db, signal_ids)
        signal_dicts: list[dict[str, str | None]] = []
        for row in rows:
            source_type = str(row["source_type"] or "")
            signal_dicts.append(
                {
                    "source_label": _DISCOVERY_LABELS.get(source_type, source_type),
                    "title": str(row["title"] or ""),
                    "summary": str(row["summary"] or ""),
                    "url": str(row["url"]) if row["url"] else None,
                }
            )
        return telegram_formatting.format_signal_explain(signal_dicts)

    if command.name in _QUERY_REQUIRED and not command.query:
        return _usage(command.name)

    if command.name in _ID_REQUIRED:
        entity_id = _parse_positive_int(command.query)
        if entity_id is None:
            return _usage(command.name)

        if command.name == CommandName.PLAN:
            current_goal = await active_goals.get_current(db)
            try:
                plan = await editorial_planner.create_persisted_editorial_plan(
                    db,
                    [entity_id],
                    goal_id=current_goal.id if current_goal else None,
                )
            except LookupError:
                return _not_found("Signal", entity_id)
            _remember_plan(chat_id, plan)
            await _persist_state(db, chat_id)
            return _format_plan_created(plan)

        if command.name == CommandName.APPROVE:
            try:
                plan = await editorial_planner.transition_editorial_plan(
                    db,
                    entity_id,
                    EditorialPlanStatus.APPROVED,
                )
            except LookupError:
                return _not_found("Plan", entity_id)
            except editorial_planner.EditorialPlanTransitionError as exc:
                return _invalid_transition(str(exc))
            _remember_plan(chat_id, plan)
            await _persist_state(db, chat_id)
            response = _format_plan_updated(plan, verb="approved")
            if plan.proposal.recommended_action == RecommendedAction.MVP:
                offer = telegram_formatting.format_handoff_offer_after_approve(
                    plan.plan_id
                )
                response += "\n" + offer
            return response

        if command.name == CommandName.DISCARD_PLAN:
            try:
                plan = await editorial_planner.transition_editorial_plan(
                    db,
                    entity_id,
                    EditorialPlanStatus.DISCARDED,
                )
            except LookupError:
                return _not_found("Plan", entity_id)
            except editorial_planner.EditorialPlanTransitionError as exc:
                return _invalid_transition(str(exc))
            _remember_plan(chat_id, plan)
            await _persist_state(db, chat_id)
            return _format_plan_updated(plan, verb="discarded")

        if command.name == CommandName.DRAFT:
            current_goal = await active_goals.get_current(db)
            try:
                draft = await draft_generator.create_persisted_editorial_draft(
                    db,
                    entity_id,
                    goal_id=current_goal.id if current_goal else None,
                )
            except LookupError:
                return _not_found("Plan", entity_id)
            except draft_generator.EditorialDraftConflictError as exc:
                return (
                    "<b>Draft already exists</b>\n"
                    f"plan: <code>#{exc.plan_id}</code>\n"
                    f"draft: <code>#{exc.draft_id}</code>\n"
                    f"next: /show_draft {exc.draft_id}"
                )
            except draft_generator.DraftGenerationStateError as exc:
                return _invalid_transition(str(exc))
            _remember_draft(chat_id, draft)
            await _persist_state(db, chat_id)
            return _format_draft_created(draft)

        if command.name == CommandName.SHOW_PLAN:
            try:
                plan = await editorial_planner.get_persisted_editorial_plan(
                    db,
                    entity_id,
                )
            except LookupError:
                return _not_found("Plan", entity_id)
            _remember_plan(chat_id, plan)
            await _persist_state(db, chat_id)
            return telegram_formatting.format_plan_summary(plan)

        if command.name == CommandName.SHOW_DRAFT:
            try:
                draft = await draft_generator.get_persisted_editorial_draft(
                    db,
                    entity_id,
                )
            except LookupError:
                return _not_found("Draft", entity_id)
            _remember_draft(chat_id, draft)
            await _persist_state(db, chat_id)
            return telegram_formatting.format_draft_summary(draft)

        if command.name == CommandName.MVP_HANDOFF:
            try:
                pack = await mvp_handoff.create_mvp_handoff_pack(db, entity_id)
            except LookupError:
                return _not_found("Plan", entity_id)
            except mvp_handoff.MvpHandoffStateError as exc:
                return _invalid_transition(str(exc))
            _set_pending(chat_id, command_name=None, target_id=None)
            await _persist_state(db, chat_id)
            return _format_mvp_handoff(pack)

        if command.name == CommandName.LINKEDIN:
            try:
                post, llm_used = await linkedin_writer.build_linkedin_post(
                    db, entity_id
                )
            except LookupError:
                return _not_found("Plan", entity_id)
            return telegram_formatting.format_linkedin_post(
                post,
                plan_id=entity_id,
                llm_used=llm_used,
            )

        if command.name == CommandName.LINKEDIN_PROMPT:
            try:
                kit = await linkedin_writer.build_linkedin_prompt_kit(
                    db, entity_id
                )
            except LookupError:
                return _not_found("Plan", entity_id)
            return telegram_formatting.format_linkedin_prompt_kit(kit)

    if command.name == CommandName.PAPERS:
        raw_query = command.query or ""
        candidates, normalized_query = await _discover_refs(
            db,
            raw_query,
            source_names=("arxiv",),
            message_id=message_id,
        )
        heading = _query_heading("Papers", raw_query, normalized_query)
        formatted = await _format_query_results(
            db,
            heading=heading,
            candidates=candidates,
            normalized_query=normalized_query,
        )
        suggestions = await _candidates_to_suggestions(db, candidates)
        _remember_signal_ids(chat_id, _suggestion_signal_ids(suggestions))
        await _persist_state(db, chat_id)
        return formatted

    if command.name == CommandName.NEWS:
        raw_query = command.query or ""
        candidates, normalized_query = await _discover_refs(
            db,
            raw_query,
            source_names=("hackernews",),
            message_id=message_id,
        )
        heading = _query_heading("Noticias", raw_query, normalized_query)
        formatted = await _format_query_results(
            db,
            heading=heading,
            candidates=candidates,
            normalized_query=normalized_query,
        )
        suggestions = await _candidates_to_suggestions(db, candidates)
        _remember_signal_ids(chat_id, _suggestion_signal_ids(suggestions))
        await _persist_state(db, chat_id)
        return formatted

    if command.name == CommandName.SIGNALS:
        raw_query = command.query or ""
        candidates, normalized_query = await _discover_refs(
            db,
            raw_query,
            message_id=message_id,
        )
        heading = _query_heading("Señales", raw_query, normalized_query)
        formatted = await _format_query_results(
            db,
            heading=heading,
            candidates=candidates,
            normalized_query=normalized_query,
        )
        suggestions = await _candidates_to_suggestions(db, candidates)
        _remember_signal_ids(chat_id, _suggestion_signal_ids(suggestions))
        await _persist_state(db, chat_id)
        return formatted

    if command.name == CommandName.GITHUB_INSIGHTS:
        if not settings.priority_github_repo_list:
            return "<b>GitHub insights</b>\nNo priority repositories configured."
        candidates = await _github_refs(db, message_id=message_id)
        suggestions = await _candidates_to_suggestions(db, candidates)
        result = await _format_query_results(
            db,
            heading="GitHub insights",
            candidates=candidates,
        )
        _remember_signal_ids(chat_id, _suggestion_signal_ids(suggestions))
        await _persist_state(db, chat_id)
        return result

    if command.name == CommandName.WEEKLY:
        summary = await build_weekly_summary(
            db,
            query=settings.weekly_discovery_query,
            message_id=message_id,
        )
        if summary is None:
            return "<b>Weekly summary</b>\nNo useful signals found this run."
        _remember_signal_ids(
            chat_id,
            [
                signal.signal_id
                for signal in summary.top_signals
                if signal.signal_id is not None
            ],
        )
        await _persist_state(db, chat_id)
        return telegram_formatting.format_weekly_summary(summary)

    if command.name == CommandName.MVP_IDEAS:
        idea = await build_mvp_idea(
            db,
            command.query or "",
            message_id=message_id,
        )
        _remember_signal_ids(chat_id, idea.signal_ids)
        await _persist_state(db, chat_id)
        return telegram_formatting.format_mvp_idea(idea)

    return telegram_formatting.format_help()


async def handle_operator_text(
    raw_text: str,
    db: aiosqlite.Connection,
    *,
    message_id: int | None = None,
    chat_id: int | None = None,
) -> str | None:
    stripped = raw_text.strip()
    if not stripped:
        return None

    await _load_state(db, chat_id)

    if is_command_text(stripped):
        return await handle_command(
            stripped,
            db,
            message_id=message_id,
            chat_id=chat_id,
        )

    natural_command = _natural_command(stripped, chat_id=chat_id)
    if natural_command is not None:
        command_text = f"/{natural_command.name.value}"
        if natural_command.query:
            command_text = f"{command_text} {natural_command.query}"
        return await handle_command(
            command_text,
            db,
            message_id=message_id,
            chat_id=chat_id,
        )

    return telegram_formatting.format_note_capture_ack(stripped)
