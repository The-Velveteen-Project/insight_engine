"""
Helpers to format compact Telegram responses with HTML escaping.
"""

from __future__ import annotations

from html import escape

from app.schemas.commands import MvpIdeaSuggestion, SignalSuggestion, WeeklySummary
from app.schemas.drafts import PersistedEditorialDraft
from app.schemas.editorial import EditorialPlanStatus, PersistedEditorialPlan
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
            "Puedo buscar señales, crear planes, aprobarlos y sacar drafts.",
            "",
            "Ejemplos:",
            "• signals climate risk",
            "• papers dengue surveillance",
            "• github_insights",
            "• plan 12",
            "• aprueba el plan 4",
            "• draft 4",
            "• show_draft 2",
            "• mvp_handoff 7",
            "• weekly",
            "",
            "También acepto slash commands si prefieres ese modo.",
        ]
    )


def format_greeting() -> str:
    return "\n".join(
        [
            "<b>Velveteen Operator</b>",
            (
                "Hola, Carlos. Estoy listo para buscar señales, ordenar "
                "hallazgos y mover una idea hasta plan o draft."
            ),
            "",
            "Puedes decirme cosas como:",
            "• signals climate risk",
            "• github_insights",
            "• weekly",
            "• qué sigue",
            "",
            (
                "Si ya tienes una señal útil, también puedo convertirla en "
                "plan y seguir desde ahí."
            ),
        ]
    )


def format_gratitude() -> str:
    return "\n".join(
        [
            "<b>De una</b>",
            "Cuando quieras seguimos.",
            (
                "Puedo buscar señales, revisar repos, armar un plan o "
                "mostrarte el último draft."
            ),
        ]
    )


def format_soft_unknown(text: str) -> str:
    return "\n".join(
        [
            "<b>No tomé eso como una instrucción operativa</b>",
            f"Recibí: <code>{escape_text(compact_text(text, 80))}</code>",
            "Puedo ayudarte mejor si me pides una acción concreta, por ejemplo:",
            "• signals climate risk",
            "• github_insights",
            "• weekly",
            "• qué sigue",
        ]
    )


def _signal_lead(suggestions: list[SignalSuggestion]) -> str:
    count = len(suggestions)
    lead = suggestions[0]
    if count == 1:
        return (
            "Encontré una señal útil. "
            f"La lectura más sobria por ahora apunta a "
            f"<code>{escape_text(lead.suggested_action.value)}</code>."
        )
    if lead.suggested_action.value == "mvp":
        return (
            f"Encontré {count} señales con buena convergencia. "
            "La más fuerte sí parece justificar una exploración de MVP."
        )
    return (
        f"Encontré {count} señales útiles. "
        "La más fuerte apunta a "
        f"<code>{escape_text(lead.suggested_action.value)}</code>, "
        "no a un MVP todavía."
    )


def format_signal_suggestions(
    heading: str,
    suggestions: list[SignalSuggestion],
) -> str:
    if not suggestions:
        return f"<b>{escape_text(heading)}</b>\nNo useful signals found."

    lines = [
        f"<b>{escape_text(heading)}</b>",
        _signal_lead(suggestions),
    ]
    for suggestion in suggestions:
        signal_prefix = f"#{suggestion.signal_id} " if suggestion.signal_id else ""
        title_text = escape_text(signal_prefix + compact_text(suggestion.title, 80))
        why_text = escape_text(compact_text(suggestion.why_it_matters, 120))
        lines.extend(
            [
                f"• <b>{title_text}</b>",
                f"  why: {why_text}",
                (
                    "  action: "
                    f"<code>{escape_text(suggestion.suggested_action.value)}</code>"
                    f" | score: <code>{suggestion.relevance_score:.2f}</code>"
                ),
            ]
        )
    first = suggestions[0]
    if first.signal_id is not None:
        lines.extend(
            [
                "",
                (
                    "recommendation: start with "
                    f"<code>#{first.signal_id}</code> as "
                    f"<code>{escape_text(first.suggested_action.value)}</code>"
                ),
                "next: hazme un plan del primero",
            ]
        )
    return "\n".join(lines)


def format_weekly_summary(summary: WeeklySummary) -> str:
    lines = [
        "<b>Weekly summary</b>",
        f"focus: <code>{escape_text(summary.query)}</code>",
        (
            "reading: "
            f"the strongest line this week looks like "
            f"<code>{escape_text(summary.editorial_action.value)}</code>, "
            "with one main angle worth developing."
        ),
        "signals:",
    ]
    for signal in summary.top_signals:
        signal_prefix = f"#{signal.signal_id} " if signal.signal_id else ""
        lines.append(f"• {escape_text(signal_prefix + compact_text(signal.title, 70))}")
    lines.extend(
        [
            (
                "editorial: "
                f"<code>{escape_text(summary.editorial_action.value)}</code>"
                f" — {escape_text(compact_text(summary.editorial_angle, 100))}"
            ),
            (
                "mvp: "
                f"<code>{escape_text(summary.mvp_action.value)}</code>"
                f" — {escape_text(compact_text(summary.mvp_summary, 110))}"
            ),
            f"next: {escape_text(compact_text(summary.next_step, 110))}",
            "follow-up: hazme un plan del primero",
        ]
    )
    return "\n".join(lines)


def format_mvp_idea(idea: MvpIdeaSuggestion) -> str:
    signal_text = ", ".join(str(signal_id) for signal_id in idea.signal_ids) or "n/a"
    lines = [
        "<b>MVP idea</b>",
        f"query: <code>{escape_text(idea.query)}</code>",
        f"action: <code>{escape_text(idea.recommended_action.value)}</code>",
        f"thesis: {escape_text(compact_text(idea.thesis, 110))}",
        f"problem: {escape_text(compact_text(idea.problem, 110))}",
        f"why: {escape_text(compact_text(idea.why_it_matters, 110))}",
        f"sources: {escape_text(', '.join(idea.possible_sources))}",
        f"system: {escape_text(compact_text(idea.system_type, 80))}",
        f"fit: {escape_text(compact_text(idea.portfolio_fit, 100))}",
        f"signals: <code>{signal_text}</code>",
    ]
    if idea.signal_ids:
        lines.append("next: hazme un plan del primero")
    return "\n".join(lines)


def format_note_capture_ack(text: str) -> str:
    return "\n".join(
        [
            "<b>Lo registré como señal manual</b>",
            escape_text(compact_text(text, 140)),
            "Puedo seguir con una de estas rutas:",
            "• busca papers sobre este tema",
            "• busca señales relacionadas",
            "• qué sigue",
            "• weekly",
        ]
    )


def _plan_next_step(plan: PersistedEditorialPlan) -> str:
    if plan.status == EditorialPlanStatus.DRAFT:
        return f"/approve {plan.plan_id} or /discard_plan {plan.plan_id}"
    if plan.status == EditorialPlanStatus.APPROVED:
        if plan.proposal.recommended_action.value == "mvp":
            return f"/draft {plan.plan_id} or /mvp_handoff {plan.plan_id}"
        return f"/draft {plan.plan_id} or keep it approved"
    if plan.status == EditorialPlanStatus.SAVED:
        return "keep for later"
    return "archived for now"


def format_plan_summary(
    plan: PersistedEditorialPlan,
    *,
    heading: str | None = None,
) -> str:
    proposal = plan.proposal
    signal_text = ", ".join(str(signal_id) for signal_id in proposal.signal_ids)
    header = heading or f"Plan #{plan.plan_id}"
    lines = [
        f"<b>{escape_text(header)}</b>",
        f"status: <code>{escape_text(plan.status.value)}</code>",
        f"action: <code>{escape_text(proposal.recommended_action.value)}</code>",
        f"confidence: <code>{proposal.confidence:.2f}</code>",
        f"why: {escape_text(compact_text(proposal.why_it_matters, 110))}",
        f"signals: <code>{escape_text(signal_text)}</code>",
        f"next: {escape_text(_plan_next_step(plan))}",
    ]
    if plan.status == EditorialPlanStatus.DRAFT:
        lines.append("you can also say: apruébalo")
    elif plan.status == EditorialPlanStatus.APPROVED:
        lines.append("you can also say: hazlo")
    return "\n".join(lines)


def format_draft_short_version(draft: PersistedEditorialDraft) -> str:
    content = draft.draft.content
    return "\n".join(
        [
            f"<b>Draft #{draft.draft_id} — short version</b>",
            f"title: {escape_text(compact_text(content.working_title, 90))}",
            escape_text(compact_text(content.short_version, 220)),
            f"cta: {escape_text(compact_text(content.cta, 90))}",
            "next: if you want, I can show the full draft again",
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
        f"status: <code>{escape_text(draft.status.value)}</code>",
        f"plan: <code>#{draft.plan_id}</code>",
        f"title: {escape_text(compact_text(content.working_title, 90))}",
        f"short: {escape_text(compact_text(content.short_version, 120))}",
        f"cta: {escape_text(compact_text(content.cta, 90))}",
        "next: revise manually or keep for later",
        "if needed: muéstramelo",
    ]
    return "\n".join(lines)


def format_mvp_handoff_summary(pack: MvpHandoffPack) -> str:
    signal_text = ", ".join(str(signal_id) for signal_id in pack.signal_ids)
    lines = [
        "<b>MVP handoff ready</b>",
        f"plan: <code>#{pack.plan_id}</code>",
        f"signals: <code>{signal_text}</code>",
        f"thesis: {escape_text(compact_text(pack.thesis, 100))}",
        f"scope: {escape_text(compact_text(pack.scope_summary, 120))}",
        f"builder: <code>{escape_text(pack.builder_target)}</code>",
        f"auditor: <code>{escape_text(pack.auditor_target)}</code>",
        "next: use the API handoff payload or paste the builder prompt into Codex",
    ]
    return "\n".join(lines)
