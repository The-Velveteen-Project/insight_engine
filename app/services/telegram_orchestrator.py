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
from typing import Literal

import aiosqlite

from app.core.config import settings
from app.db.queries import get_signal_by_source_identity
from app.schemas.commands import (
    CommandName,
    MvpIdeaSuggestion,
    ParsedTelegramCommand,
    SignalSuggestion,
    WeeklySummary,
)
from app.schemas.discovery import SignalCandidate
from app.schemas.drafts import PersistedEditorialDraft
from app.schemas.editorial import (
    EditorialPlan,
    EditorialPlanStatus,
    PersistedEditorialPlan,
    RecommendedAction,
)
from app.schemas.github import GitHubInsightCandidate
from app.schemas.mvp_handoff import MvpHandoffPack
from app.services import (
    discovery_service,
    draft_generator,
    editorial_planner,
    github_insight_service,
    mvp_handoff,
)
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
}
_DISCOVERY_LABELS: dict[str, str] = {
    "arxiv": "arXiv API",
    "hackernews": "Hacker News Algolia",
    "github": "GitHub REST",
}
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
_FIRST_TOKENS: dict[str, CommandName] = {
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


@dataclass(frozen=True)
class _CandidateRef:
    source_type: Literal["arxiv", "hackernews", "github"]
    source_id: str
    title: str
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


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _get_state(chat_id: int | None) -> _ChatState | None:
    if chat_id is None:
        return None
    return _CHAT_STATE.setdefault(chat_id, _ChatState(last_signal_ids=[]))


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
                "<b>No pending action</b>",
                "Try one of these:",
                "• signals climate risk",
                "• github_insights",
                "• show_plan 12",
            ]
        )

    if state.pending_command == CommandName.PLAN:
        return (
            "<b>Next step</b>\n"
            f"Create a plan from signal <code>#{state.pending_target_id}</code>.\n"
            "You can say <code>hazlo</code> or <code>plan del primero</code>."
        )
    if state.pending_command == CommandName.APPROVE:
        return (
            "<b>Next step</b>\n"
            f"Approve plan <code>#{state.pending_target_id}</code>.\n"
            "You can say <code>hazlo</code> or <code>apruébalo</code>."
        )
    if state.pending_command == CommandName.DRAFT:
        return (
            "<b>Next step</b>\n"
            f"Generate a draft from plan <code>#{state.pending_target_id}</code>.\n"
            "You can say <code>hazlo</code> or <code>draft</code>."
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
            summary=candidate.summary,
            relevance_score=candidate.relevance_score,
            relevance_note=candidate.relevance_note,
        )
    return _CandidateRef(
        source_type="github",
        source_id=candidate.source_id,
        title=candidate.title,
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
    return await editorial_planner.plan_editorial(
        db,
        signal_ids,
        use_generation=False,
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


async def build_weekly_summary(
    db: aiosqlite.Connection,
    *,
    query: str | None = None,
    message_id: int | None = None,
) -> WeeklySummary | None:
    resolved_query = query or settings.weekly_discovery_query
    external, _ = await _discover_refs(db, resolved_query, message_id=message_id)
    github_refs = await _github_refs(db, message_id=message_id)
    combined = sorted(
        external + github_refs,
        key=lambda item: item.relevance_score,
        reverse=True,
    )
    if not combined:
        return None

    signal_ids = await _top_signal_ids(db, combined, limit=3)
    if not signal_ids:
        return None

    suggestions = await _candidates_to_suggestions(db, combined[:3])
    plan = await _plan_for_signal_ids(db, signal_ids)
    mvp_idea = await build_mvp_idea(
        db,
        resolved_query,
        message_id=message_id,
        candidate_refs=combined,
    )
    return WeeklySummary(
        query=resolved_query,
        top_signals=suggestions[:3],
        editorial_action=plan.recommended_action,
        editorial_angle=plan.angle,
        mvp_action=mvp_idea.recommended_action,
        mvp_summary=mvp_idea.thesis,
        next_step=_next_step(plan, signal_ids),
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
        )

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
    )


def _query_heading(label: str, raw_query: str, normalized_query: str) -> str:
    """Build a heading that shows the translated query when it differs."""
    nq = normalized_query.strip()
    rq = raw_query.strip()
    if nq and nq.lower() != rq.lower():
        return f"{label} · {nq} [{rq}]"
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
    command = parse_command(raw_text)

    if command.name == CommandName.HELP or command.name == CommandName.UNKNOWN:
        if command.name == CommandName.HELP and command.query == "__greeting__":
            return telegram_formatting.format_greeting()
        if command.name == CommandName.HELP and command.query == "__gratitude__":
            return telegram_formatting.format_gratitude()
        if command.name == CommandName.HELP and command.query == "__short_draft__":
            state = _get_state(chat_id)
            draft_id = state.last_draft_id if state is not None else None
            if draft_id is None:
                return _pending_help(state)
            draft = await draft_generator.get_persisted_editorial_draft(db, draft_id)
            _remember_draft(chat_id, draft)
            return telegram_formatting.format_draft_short_version(draft)
        if command.name == CommandName.HELP and command.query == "__pending__":
            return _pending_help(_get_state(chat_id))
        if command.name == CommandName.UNKNOWN:
            return telegram_formatting.format_soft_unknown(command.raw_text)
        return telegram_formatting.format_help()

    if command.name in _QUERY_REQUIRED and not command.query:
        return _usage(command.name)

    if command.name in _ID_REQUIRED:
        entity_id = _parse_positive_int(command.query)
        if entity_id is None:
            return _usage(command.name)

        if command.name == CommandName.PLAN:
            try:
                plan = await editorial_planner.create_persisted_editorial_plan(
                    db,
                    [entity_id],
                )
            except LookupError:
                return _not_found("Signal", entity_id)
            _remember_plan(chat_id, plan)
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
            return _format_plan_updated(plan, verb="approved")

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
            return _format_plan_updated(plan, verb="discarded")

        if command.name == CommandName.DRAFT:
            try:
                draft = await draft_generator.create_persisted_editorial_draft(
                    db,
                    entity_id,
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
            return telegram_formatting.format_draft_summary(draft)

        if command.name == CommandName.MVP_HANDOFF:
            try:
                pack = await mvp_handoff.create_mvp_handoff_pack(db, entity_id)
            except LookupError:
                return _not_found("Plan", entity_id)
            except mvp_handoff.MvpHandoffStateError as exc:
                return _invalid_transition(str(exc))
            _set_pending(chat_id, command_name=None, target_id=None)
            return _format_mvp_handoff(pack)

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
        return telegram_formatting.format_weekly_summary(summary)

    if command.name == CommandName.MVP_IDEAS:
        idea = await build_mvp_idea(
            db,
            command.query or "",
            message_id=message_id,
        )
        _remember_signal_ids(chat_id, idea.signal_ids)
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
