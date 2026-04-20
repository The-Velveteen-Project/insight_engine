"""
Editorial planner for Phase 6.

Deterministic rules decide the action and confidence.
The LLM is used only to structure the narrative fields.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import aiosqlite

from app.db.queries import (
    get_editorial_plan_by_id,
    get_signals_by_ids,
    insert_editorial_plan,
    update_editorial_plan_status,
)
from app.schemas.editorial import (
    DecisionBasis,
    DraftOutline,
    EditorialGenerationInput,
    EditorialPlan,
    EditorialPlanStatus,
    EditorialSignalContext,
    GeneratedEditorialDraft,
    PersistedEditorialPlan,
    RecommendedAction,
)
from app.services.generation import get_editorial_generator

logger = logging.getLogger(__name__)


class EditorialPlanTransitionError(Exception):
    """Raised when a requested editorial plan state change is invalid."""

_MVP_KEYWORDS = {
    "agent",
    "api",
    "benchmark",
    "build",
    "evaluation",
    "fastapi",
    "mvp",
    "pipeline",
    "prototype",
    "system",
    "tool",
    "workflow",
}
_NOTE_KEYWORDS = {
    "analysis",
    "bayesian",
    "benchmark",
    "climate",
    "education",
    "health",
    "model",
    "paper",
    "research",
    "risk",
    "study",
}
_POST_KEYWORDS = {
    "news",
    "signal",
    "trend",
    "workflow",
}
_PUBLIC_ANGLE_KEYWORDS = _POST_KEYWORDS | {"insight", "lesson", "update"}

# Phase 7 state machine:
# - draft -> approved | saved | discarded
# - approved -> saved
# - saved -> terminal
# - discarded -> terminal
_ALLOWED_STATUS_TRANSITIONS: dict[EditorialPlanStatus, set[EditorialPlanStatus]] = {
    EditorialPlanStatus.DRAFT: {
        EditorialPlanStatus.APPROVED,
        EditorialPlanStatus.SAVED,
        EditorialPlanStatus.DISCARDED,
    },
    EditorialPlanStatus.APPROVED: {EditorialPlanStatus.SAVED},
    EditorialPlanStatus.SAVED: set(),
    EditorialPlanStatus.DISCARDED: set(),
}


def _signal_corpus(signal: EditorialSignalContext) -> str:
    return " ".join(
        [
            signal.source_type,
            signal.source_id or "",
            signal.title,
            signal.summary or "",
            signal.relevance_note,
        ]
    ).lower()


def _to_signal_context(row: aiosqlite.Row) -> EditorialSignalContext:
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


def _build_corpus(signals: Sequence[EditorialSignalContext]) -> str:
    return " ".join(_signal_corpus(signal) for signal in signals)


def _has_any(corpus: str, keywords: set[str]) -> bool:
    return any(keyword in corpus for keyword in keywords)


def _matched_keywords(corpus: str, keywords: set[str]) -> set[str]:
    return {keyword for keyword in keywords if keyword in corpus}


def _max_score(signals: Sequence[EditorialSignalContext]) -> float:
    return max((signal.relevance_score for signal in signals), default=0.0)


def _source_types(signals: Sequence[EditorialSignalContext]) -> set[str]:
    return {signal.source_type for signal in signals}


def _signal_markers(signal: EditorialSignalContext) -> set[str]:
    corpus = _signal_corpus(signal)
    return (
        _matched_keywords(corpus, _MVP_KEYWORDS)
        | _matched_keywords(corpus, _NOTE_KEYWORDS)
        | _matched_keywords(corpus, _PUBLIC_ANGLE_KEYWORDS)
    )


def _coherent_theme(signals: Sequence[EditorialSignalContext]) -> bool:
    if not signals:
        return False
    marker_sets = [_signal_markers(signal) for signal in signals]
    if len(marker_sets) == 1:
        return bool(marker_sets[0])
    shared = set.intersection(*marker_sets) if marker_sets else set()
    return bool(shared)


def _incoherent_mix(signals: Sequence[EditorialSignalContext]) -> bool:
    return len(signals) > 1 and not _coherent_theme(signals)


def _public_angle_strength(signals: Sequence[EditorialSignalContext]) -> int:
    return sum(
        len(_matched_keywords(_signal_corpus(signal), _PUBLIC_ANGLE_KEYWORDS))
        for signal in signals
    )


def _research_strength(signals: Sequence[EditorialSignalContext]) -> int:
    return sum(
        len(_matched_keywords(_signal_corpus(signal), _NOTE_KEYWORDS))
        for signal in signals
    )


def _build_strength(signals: Sequence[EditorialSignalContext]) -> int:
    return sum(
        len(_matched_keywords(_signal_corpus(signal), _MVP_KEYWORDS))
        for signal in signals
    )


def _choose_action(signals: Sequence[EditorialSignalContext]) -> RecommendedAction:
    sources = _source_types(signals)
    max_score = _max_score(signals)
    has_github = "github" in sources
    has_external = bool({"arxiv", "hackernews"} & sources)
    coherent_theme = _coherent_theme(signals)
    incoherent_mix = _incoherent_mix(signals)
    public_angle_strength = _public_angle_strength(signals)
    research_strength = _research_strength(signals)
    build_strength = _build_strength(signals)

    if max_score < 0.3 and len(signals) == 1:
        return RecommendedAction.ARCHIVE
    if incoherent_mix and max_score < 0.6:
        return RecommendedAction.ARCHIVE
    if (
        len(signals) > 1
        and has_github
        and has_external
        and coherent_theme
        and build_strength >= 2
        and max_score >= 0.75
    ):
        return RecommendedAction.MVP
    if (
        max_score >= 0.7
        and coherent_theme
        and not incoherent_mix
        and public_angle_strength >= 2
        and public_angle_strength > research_strength
    ):
        return RecommendedAction.POST
    if max_score >= 0.45:
        return RecommendedAction.NOTE
    return RecommendedAction.ARCHIVE


def _matched_rule(
    signals: Sequence[EditorialSignalContext],
    action: RecommendedAction,
) -> str:
    sources = _source_types(signals)
    max_score = _max_score(signals)
    incoherent_mix = _incoherent_mix(signals)

    if action == RecommendedAction.ARCHIVE:
        if max_score < 0.3 and len(signals) == 1:
            return "weak_single_signal_archive"
        if incoherent_mix:
            return "incoherent_multi_signal_archive"
        return "default_archive"
    if action == RecommendedAction.MVP:
        return "mixed_github_external_build_signal"
    if action == RecommendedAction.NOTE:
        if incoherent_mix:
            return "incoherent_but_salvageable_note"
        if "arxiv" in sources and max_score >= 0.55:
            return "useful_but_immature_note"
        return "default_note"
    return "strong_public_angle_post"


def _confidence(
    signals: Sequence[EditorialSignalContext], action: RecommendedAction
) -> float:
    score = 0.30 + min(_max_score(signals), 0.40)
    if len(signals) > 1:
        score += 0.10
    if len(_source_types(signals)) > 1 and not _incoherent_mix(signals):
        score += 0.10
    if _coherent_theme(signals):
        score += 0.08
    if action == RecommendedAction.MVP:
        score += 0.07
    if action == RecommendedAction.ARCHIVE:
        score -= 0.20
    if _incoherent_mix(signals):
        score -= 0.12
    return round(min(max(score, 0.2), 0.95), 2)


def _confidence_factors(
    signals: Sequence[EditorialSignalContext],
    action: RecommendedAction,
) -> list[str]:
    factors = ["base=0.30", f"max_relevance={min(_max_score(signals), 0.40):.2f}"]
    if len(signals) > 1:
        factors.append("multi_signal_bonus=0.10")
    if len(_source_types(signals)) > 1 and not _incoherent_mix(signals):
        factors.append("cross_source_bonus=0.10")
    if _coherent_theme(signals):
        factors.append("coherent_theme_bonus=0.08")
    if action == RecommendedAction.MVP:
        factors.append("mvp_bonus=0.07")
    if action == RecommendedAction.ARCHIVE:
        factors.append("archive_penalty=-0.20")
    if _incoherent_mix(signals):
        factors.append("incoherent_mix_penalty=-0.12")
    return factors


def _rationale_hint(
    signals: Sequence[EditorialSignalContext],
    action: RecommendedAction,
) -> str:
    primary = signals[0]
    if action == RecommendedAction.MVP:
        return (
            "The combined signals suggest a small applied build is more useful "
            "than a pure commentary piece."
        )
    if action == RecommendedAction.NOTE:
        return (
            "The signal is strong enough for a technical note that explains the "
            "method, lesson, or system implication."
        )
    if action == RecommendedAction.POST:
        return (
            "The signal is useful, but it is better framed as a concise public "
            "insight than as a deep technical artifact."
        )
    return (
        f"The current signal around '{primary.title}' looks too weak or too early "
        "to justify pushing it now."
    )


def _angle_hint(
    signals: Sequence[EditorialSignalContext],
    action: RecommendedAction,
) -> str:
    primary = signals[0]
    if action == RecommendedAction.MVP:
        return (
            f"Translate '{primary.title}' into one narrow build hypothesis with "
            "clear technical scope and portfolio reuse."
        )
    if action == RecommendedAction.NOTE:
        return (
            f"Explain the technical lesson behind '{primary.title}' with evidence, "
            "constraints, and concrete implementation implications."
        )
    if action == RecommendedAction.POST:
        return (
            f"Condense '{primary.title}' into one sharp claim plus one technical "
            "observation worth sharing publicly."
        )
    return (
        f"Record why '{primary.title}' is not worth developing further right now, "
        "without overstating the signal."
    )


def _fallback_narrative(
    signals: Sequence[EditorialSignalContext],
    action: RecommendedAction,
) -> GeneratedEditorialDraft:
    primary = signals[0]
    secondary_clause = ""
    if len(signals) > 1:
        secondary_clause = " The related signals reinforce the same theme."

    if action == RecommendedAction.MVP:
        why = (
            f"The primary signal '{primary.title}' looks actionable enough to test "
            "as a small applied build rather than leaving it as commentary."
            f"{secondary_clause}"
        )
        angle = f"Small applied build around {primary.title.lower()}"
        outline = DraftOutline(
            hook=f"Start from the concrete problem implied by '{primary.title}'.",
            points=[
                "Define the smallest buildable scope and technical constraints.",
                "Describe what success would look like and what to measure first.",
            ],
            closing="End with the smallest next experiment worth running.",
        )
        value = (
            "This can become a portfolio artifact that shows problem framing, "
            "technical scope control, and applied execution."
        )
    elif action == RecommendedAction.NOTE:
        why = (
            f"The signal '{primary.title}' is better suited to a technical note "
            "than to a fast public take."
            f"{secondary_clause}"
        )
        angle = f"Technical lesson from {primary.title.lower()}"
        outline = DraftOutline(
            hook="Open with the signal and the concrete problem it points to.",
            points=[
                "Extract the technical lesson, method, or system implication.",
                "Clarify one implementation constraint or tradeoff.",
            ],
            closing="Close with one implication for future builds or research.",
        )
        value = (
            "This adds evidence of technical judgment and helps turn scattered "
            "signals into coherent written work."
        )
    elif action == RecommendedAction.POST:
        why = (
            f"The signal '{primary.title}' is strong enough for a concise public "
            "insight, but not yet for a larger artifact."
        )
        angle = f"One clear public insight from {primary.title.lower()}"
        outline = DraftOutline(
            hook="Open with the concrete signal worth paying attention to.",
            points=[
                "Add one technical interpretation grounded in the evidence.",
                "Add one narrow implication rather than a broad claim.",
            ],
            closing="Close with one next question worth tracking.",
        )
        value = (
            "This keeps the public narrative active without overcommitting to a "
            "larger build or note."
        )
    else:
        why = (
            f"The signal '{primary.title}' should be archived for now because the "
            "evidence is weak, early, or not sufficiently differentiated."
        )
        angle = f"Archive rationale for {primary.title.lower()}"
        outline = DraftOutline(
            hook="State clearly what the signal was.",
            points=[
                "Explain why it is not strong enough to pursue now.",
                "Note what kind of future evidence would justify revisiting it.",
            ],
            closing="End with a narrow condition for reconsideration.",
        )
        value = (
            "A clear archive decision protects focus and prevents weak signals from "
            "turning into noisy output."
        )

    return GeneratedEditorialDraft(
        why_it_matters=why,
        angle=angle,
        draft_outline=outline,
        portfolio_value=value,
    )


def _to_persisted_editorial_plan(row: aiosqlite.Row) -> PersistedEditorialPlan:
    return PersistedEditorialPlan(
        plan_id=int(row["id"]),
        status=EditorialPlanStatus(str(row["status"])),
        proposal=EditorialPlan.model_validate_json(str(row["proposal_json"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        reviewed_at=row["reviewed_at"],
    )


async def plan_editorial(
    db: aiosqlite.Connection,
    signal_ids: list[int],
    *,
    use_generation: bool = True,
) -> EditorialPlan:
    rows = await get_signals_by_ids(db, signal_ids)
    if not rows:
        raise LookupError("No persisted signals were found for the requested ids.")
    if len(rows) != len(signal_ids):
        found_ids = {int(row["id"]) for row in rows}
        missing_ids = [
            signal_id for signal_id in signal_ids if signal_id not in found_ids
        ]
        missing_ids_text = ", ".join(str(item) for item in missing_ids)
        raise LookupError(
            f"Some requested signals were not found: {missing_ids_text}."
        )

    signals = [_to_signal_context(row) for row in rows]
    primary_signals = sorted(
        signals, key=lambda item: item.relevance_score, reverse=True
    )
    selected = [primary_signals[0]]
    selected.extend(signal for signal in signals if signal.id != primary_signals[0].id)
    generation_signals = selected[:3]

    action = _choose_action(generation_signals)
    confidence = _confidence(generation_signals, action)
    confidence_factors = _confidence_factors(generation_signals, action)
    generation_input = EditorialGenerationInput(
        recommended_action=action,
        confidence=confidence,
        rationale_hint=_rationale_hint(generation_signals, action),
        angle_hint=_angle_hint(generation_signals, action),
        signals=generation_signals,
    )

    generator = get_editorial_generator() if use_generation else None
    llm_used = False
    narrative = (
        await generator.generate(generation_input) if generator is not None else None
    )
    if narrative is not None:
        llm_used = True
    if narrative is None:
        logger.info("Editorial planner using deterministic fallback narrative.")
        narrative = _fallback_narrative(generation_signals, action)

    return EditorialPlan(
        signal_ids=[signal.id for signal in generation_signals],
        recommended_action=action,
        decision_basis=DecisionBasis(
            primary_signal_id=generation_signals[0].id,
            supporting_signal_ids=[signal.id for signal in generation_signals[1:]],
            source_types=[signal.source_type for signal in generation_signals],
            matched_rule=_matched_rule(generation_signals, action),
            confidence_factors=confidence_factors,
        ),
        why_it_matters=narrative.why_it_matters,
        angle=narrative.angle,
        draft_outline=narrative.draft_outline,
        portfolio_value=narrative.portfolio_value,
        confidence=confidence,
        llm_used=llm_used,
        fallback_used=not llm_used,
        needs_human_review=True,
    )


async def create_persisted_editorial_plan(
    db: aiosqlite.Connection,
    signal_ids: list[int],
) -> PersistedEditorialPlan:
    proposal = await plan_editorial(db, signal_ids)
    plan_id = await insert_editorial_plan(db, proposal)
    row = await get_editorial_plan_by_id(db, plan_id)
    if row is None:
        raise LookupError(f"Persisted editorial plan was not found: {plan_id}.")
    return _to_persisted_editorial_plan(row)


async def get_persisted_editorial_plan(
    db: aiosqlite.Connection,
    plan_id: int,
) -> PersistedEditorialPlan:
    row = await get_editorial_plan_by_id(db, plan_id)
    if row is None:
        raise LookupError(f"Editorial plan not found: {plan_id}.")
    return _to_persisted_editorial_plan(row)


async def transition_editorial_plan(
    db: aiosqlite.Connection,
    plan_id: int,
    target_status: EditorialPlanStatus,
) -> PersistedEditorialPlan:
    current = await get_persisted_editorial_plan(db, plan_id)
    allowed_targets = _ALLOWED_STATUS_TRANSITIONS[current.status]
    if target_status not in allowed_targets:
        raise EditorialPlanTransitionError(
            "Invalid editorial plan transition: "
            f"{current.status.value} -> {target_status.value}."
        )

    await update_editorial_plan_status(db, plan_id, target_status)
    return await get_persisted_editorial_plan(db, plan_id)
