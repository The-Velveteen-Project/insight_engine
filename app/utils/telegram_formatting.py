"""
Helpers to format compact Telegram responses with HTML escaping.

Tone principles:
- Spanish first, Spanish-speaker friendly.
- Direct and first-person where appropriate. No machine-output labels.
- Technical terms (plan, draft, archive, note, post, mvp) stay in English
  because that's how the commands work — mixing is intentional and clear.
- Compact: each message fits comfortably on a phone screen.
"""

from __future__ import annotations

from collections import Counter
from html import escape

from app.schemas.commands import MvpIdeaSuggestion, SignalSuggestion, WeeklySummary
from app.schemas.drafts import PersistedEditorialDraft
from app.schemas.editorial import (
    EditorialPlanStatus,
    PersistedEditorialPlan,
    RecommendedAction,
)
from app.schemas.mvp_handoff import MvpHandoffPack


def compact_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def escape_text(text: str) -> str:
    return escape(text, quote=False)


def format_help() -> str:
    return "\n".join(
        [
            "<b>Velveteen Operator</b>",
            "Busco señales, armo planes, genero drafts.",
            "",
            "Ejemplos:",
            "• signals membrane filtration",
            "• papers dengue surveillance",
            "• github_insights",
            "• plan 12",
            "• apruébalo",
            "• draft 4",
            "• show_draft 2",
            "• mvp_handoff 7",
            "• weekly",
        ]
    )


def format_greeting() -> str:
    return "\n".join(
        [
            "<b>Velveteen Operator</b>",
            "Hola, Carlos. Listo.",
            "",
            "Puedes pedirme cosas como:",
            "• signals climate risk",
            "• papers agentic workflows",
            "• github_insights",
            "• weekly",
            "• qué sigue",
        ]
    )


def format_gratitude() -> str:
    return "Cuando quieras seguimos."


def format_soft_unknown(text: str) -> str:
    return "\n".join(
        [
            f"No entendí eso: <code>{escape_text(compact_text(text, 70))}</code>",
            "Prueba con: signals X · papers X · github_insights · weekly",
        ]
    )


def _action_label(action: RecommendedAction) -> str:
    labels = {
        RecommendedAction.ARCHIVE: "archive",
        RecommendedAction.NOTE: "nota técnica",
        RecommendedAction.POST: "post",
        RecommendedAction.MVP: "MVP",
    }
    return labels.get(action, action.value)


def _signal_lead(suggestions: list[SignalSuggestion]) -> str:
    count = len(suggestions)
    lead = suggestions[0]
    action = lead.suggested_action
    top_score = lead.relevance_score

    if action == RecommendedAction.MVP and top_score >= 0.75:
        return f"{count} señales con buena convergencia. La más fuerte justifica explorar un MVP."
    if action == RecommendedAction.NOTE and top_score >= 0.45:
        return f"{count} señales. La más fuerte da para una nota técnica."
    if action == RecommendedAction.POST:
        return f"{count} señales. Hay ángulo para un post conciso."
    if top_score < 0.25:
        return f"{count} señales, ninguna supera el umbral. Las archivaría por ahora."
    return f"{count} señales. Sin base fuerte todavía — archivaría de momento."


def _signal_take(suggestions: list[SignalSuggestion]) -> str:
    lead = suggestions[0]
    top_score = lead.relevance_score
    actions = [s.suggested_action for s in suggestions[:3]]
    dominant = Counter(actions).most_common(1)[0][0]
    mixed = len(set(actions)) > 1

    if dominant == RecommendedAction.MVP and top_score >= 0.75 and not mixed:
        return "Vale la pena probar un build pequeño y acotado."
    if dominant == RecommendedAction.NOTE and not mixed:
        return "Lo más sensato es una nota técnica sobria."
    if dominant == RecommendedAction.POST:
        return "Da para un post claro, no para build todavía."
    if mixed and top_score < 0.70:
        return "Señales mezcladas — trataría como note antes que forzar un MVP."
    return "Lo dejaría en archive por ahora."


def format_signal_suggestions(
    heading: str,
    suggestions: list[SignalSuggestion],
    *,
    normalized_query: str = "",
) -> str:
    if not suggestions:
        return format_no_signals(heading, normalized_query)

    lead = _signal_lead(suggestions)
    take = _signal_take(suggestions)

    lines = [
        f"<b>{escape_text(heading)}</b>",
        f"{lead} {take}",
        "",
    ]
    for s in suggestions:
        id_prefix = f"#{s.signal_id} " if s.signal_id else ""
        title_line = escape_text(id_prefix + compact_text(s.title, 72))
        score_tag = f"({s.relevance_score:.2f}) "
        why_text = escape_text(compact_text(s.why_it_matters, 100))
        lines.append(f"• {score_tag}<b>{title_line}</b>")
        lines.append(f"  {why_text}")

    first = suggestions[0]
    if first.signal_id is not None:
        action_str = escape_text(_action_label(first.suggested_action))
        lines.extend(
            [
                "",
                f"Para avanzar: <code>plan {first.signal_id}</code> "
                f"(como {action_str})",
            ]
        )
    return "\n".join(lines)


def format_no_signals(heading: str, normalized_query: str = "") -> str:
    lines = [
        f"<b>{escape_text(heading)}</b>",
        "No encontré señales relevantes para este tema.",
    ]
    nq = normalized_query.strip()
    if nq:
        lines.append(
            f"Las fuentes (arXiv, HN) indexan en inglés. "
            f"La búsqueda fue: <code>{escape_text(nq)}</code>"
        )
    lines.append("Prueba ser más específico o usa términos en inglés.")
    return "\n".join(lines)


def format_weekly_summary(summary: WeeklySummary) -> str:
    if summary.mvp_action == RecommendedAction.MVP:
        take = "Hay base para explorar una línea de MVP pequeña."
    elif summary.editorial_action == RecommendedAction.NOTE:
        take = "Esta semana empujaría una nota técnica, no un build."
    elif summary.editorial_action == RecommendedAction.POST:
        take = "Mejor ángulo editorial que técnico para construir."
    else:
        take = "Semana conservadora — archivaría esta línea por ahora."

    action_label = _action_label(summary.editorial_action)
    lines = [
        "<b>Resumen semanal</b>",
        f"Foco: <code>{escape_text(summary.query)}</code>",
        take,
        "",
        "Señales:",
    ]
    for signal in summary.top_signals:
        prefix = f"#{signal.signal_id} " if signal.signal_id else ""
        lines.append(f"• {escape_text(prefix + compact_text(signal.title, 70))}")
    lines.extend(
        [
            "",
            f"Editorial: <code>{escape_text(action_label)}</code>"
            f" — {escape_text(compact_text(summary.editorial_angle, 90))}",
            f"MVP: <code>{escape_text(summary.mvp_action.value)}</code>"
            f" — {escape_text(compact_text(summary.mvp_summary, 90))}",
            f"Próximo: {escape_text(compact_text(summary.next_step, 100))}",
            "",
        ]
    )
    first_id = next(
        (s.signal_id for s in summary.top_signals if s.signal_id is not None), None
    )
    if first_id is not None:
        lines.append(f"Para continuar: <code>plan {first_id}</code> o <code>weekly</code>")
    else:
        lines.append("Para continuar: <code>weekly</code>")
    return "\n".join(lines)


def format_mvp_idea(idea: MvpIdeaSuggestion) -> str:
    signal_text = ", ".join(str(s) for s in idea.signal_ids) or "—"
    is_mvp = idea.recommended_action == RecommendedAction.MVP
    take = "Sí probaría un MVP pequeño." if is_mvp else "No forzaría un build todavía."
    lines = [
        "<b>Ideas de MVP</b>",
        f"Query: <code>{escape_text(idea.query)}</code>",
        f"Decisión: <code>{escape_text(idea.recommended_action.value)}</code> — {take}",
        "",
        f"{escape_text(compact_text(idea.thesis, 110))}",
        f"{escape_text(compact_text(idea.why_it_matters, 110))}",
        "",
        f"Fuentes: {escape_text(', '.join(idea.possible_sources))}",
        f"Señales: <code>{signal_text}</code>",
    ]
    if idea.signal_ids:
        lines.append(f"\nPara continuar: <code>plan {idea.signal_ids[0]}</code>")
    return "\n".join(lines)


def format_note_capture_ack(text: str) -> str:
    return "\n".join(
        [
            "Registrado.",
            f"<code>{escape_text(compact_text(text, 120))}</code>",
            "",
            "Rutas posibles:",
            "• <code>signals</code> sobre este tema",
            "• <code>papers</code> sobre esto",
            "• <code>qué sigue</code>",
        ]
    )


def _plan_action_label(plan: PersistedEditorialPlan) -> str:
    return _action_label(
        RecommendedAction(plan.proposal.recommended_action.value)
    )


def _plan_next_hint(plan: PersistedEditorialPlan) -> str:
    if plan.status == EditorialPlanStatus.DRAFT:
        return (
            f"<code>apruébalo</code>  o  "
            f"<code>discard_plan {plan.plan_id}</code>"
        )
    if plan.status == EditorialPlanStatus.APPROVED:
        if plan.proposal.recommended_action == RecommendedAction.MVP:
            return (
                f"<code>draft {plan.plan_id}</code>  o  "
                f"<code>mvp_handoff {plan.plan_id}</code>"
            )
        return f"<code>draft {plan.plan_id}</code>"
    if plan.status == EditorialPlanStatus.SAVED:
        return "guardado para más tarde"
    return "archivado"


def format_plan_summary(
    plan: PersistedEditorialPlan,
    *,
    heading: str | None = None,
) -> str:
    proposal = plan.proposal
    signal_text = ", ".join(f"#{s}" for s in proposal.signal_ids)
    header = heading or f"Plan #{plan.plan_id}"
    action_str = escape_text(_plan_action_label(plan))
    why_text = escape_text(compact_text(proposal.why_it_matters, 120))
    lines = [
        f"<b>{escape_text(header)}</b>",
        f"<code>{escape_text(plan.status.value)}</code> · "
        f"{action_str} · confianza {proposal.confidence:.2f}",
        f"Señales: <code>{escape_text(signal_text)}</code>",
        "",
        why_text,
        "",
        _plan_next_hint(plan),
    ]
    return "\n".join(lines)


def format_draft_short_version(draft: PersistedEditorialDraft) -> str:
    content = draft.draft.content
    return "\n".join(
        [
            f"<b>Draft #{draft.draft_id} — versión corta</b>",
            f"<i>{escape_text(compact_text(content.working_title, 90))}</i>",
            "",
            escape_text(compact_text(content.short_version, 220)),
            "",
            f"CTA: {escape_text(compact_text(content.cta, 90))}",
        ]
    )


def format_draft_summary(
    draft: PersistedEditorialDraft,
    *,
    heading: str | None = None,
) -> str:
    content = draft.draft.content
    header = heading or f"Draft #{draft.draft_id}"
    lines = [
        f"<b>{escape_text(header)}</b>",
        f"<code>{escape_text(draft.status.value)}</code> · plan #{draft.plan_id}",
        "",
        f"<i>{escape_text(compact_text(content.working_title, 90))}</i>",
        escape_text(compact_text(content.short_version, 160)),
        "",
        f"CTA: {escape_text(compact_text(content.cta, 90))}",
        "",
        "Para ver completo: <code>muéstramelo</code>",
    ]
    return "\n".join(lines)


def format_mvp_handoff_summary(pack: MvpHandoffPack) -> str:
    signal_text = ", ".join(str(s) for s in pack.signal_ids)
    lines = [
        "<b>MVP handoff listo</b>",
        f"Plan: <code>#{pack.plan_id}</code> · señales: <code>{signal_text}</code>",
        "",
        escape_text(compact_text(pack.thesis, 110)),
        escape_text(compact_text(pack.scope_summary, 120)),
        "",
        f"Builder: <code>{escape_text(pack.builder_target)}</code>",
        f"Auditor: <code>{escape_text(pack.auditor_target)}</code>",
        "",
        "Siguiente: copia el builder prompt al modelo que vayas a usar.",
    ]
    return "\n".join(lines)
