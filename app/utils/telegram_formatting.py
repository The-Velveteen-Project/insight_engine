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

_SOLID_SIGNAL_THRESHOLD = 0.45
_WEAK_SIGNAL_THRESHOLD = 0.25


def compact_text(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def escape_text(text: str) -> str:
    return escape(text, quote=False)


def _readable_text(text: str, *, limit: int = 220) -> str:
    return escape_text(compact_text(text, limit))


def format_help() -> str:
    return "\n".join(
        [
            "<b>Velveteen Operator</b>",
            (
                "Busco señales, te muestro links útiles y muevo una idea "
                "hasta plan o draft."
            ),
            "",
            "Ejemplos:",
            "• /start",
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
            (
                "Hola, Carlos. Puedo buscar señales, enseñarte links útiles "
                "y ordenar una línea hasta plan o draft."
            ),
            "",
            "Puedes pedirme cosas como:",
            "• signals climate risk",
            "• papers agentic workflows",
            "• github_insights",
            "• weekly",
            "• qué sigue",
        ]
    )


def format_start_message() -> str:
    return "\n".join(
        [
            "🐇 <b>Velveteen Operator</b>",
            "Hola, Carlos.",
            "",
            (
                "Soy la capa operativa de The Velveteen Project. No existo solo "
                "para listar noticias o sacar drafts: existo para ayudarte a unir "
                "lo que investigas, lo que construyes y lo que intuyes en una sola "
                "línea de trabajo con criterio."
            ),
            "",
            "<b>Qué soy</b>",
            (
                "Soy un operador editorial y de portafolio para un applied decision "
                "systems lab founder-led. Mi trabajo es convertir fragmentos dispersos "
                "en continuidad útil."
            ),
            (
                "Eso incluye señales externas, papers, actividad de repos, notas, "
                "clases, intuiciones técnicas y posibles builds."
            ),
            "No reemplazo criterio. Lo organizo.",
            "",
            "<b>Qué hago bien</b>",
            "• buscar papers, news y señales mixtas sin perder el foco",
            "• conectar lo que aparece afuera con tus repos y tu línea de trabajo",
            "• ayudarte a decidir si algo debe ir a archive, note, post o MVP",
            "• mover una señal prometedora hasta plan, aprobación y draft",
            "• decirte con honestidad cuando la base todavía no da",
            "",
            "<b>Qué no soy</b>",
            "• no soy un feed reader con maquillaje",
            "• no soy una fábrica de posts vacíos",
            "• no soy un generador de MVPs por ansiedad",
            "• no publico por ti ni tomo decisiones humanas finales",
            "",
            "<b>Limitaciones</b>",
            (
                "Dependo de lo que devuelvan las APIs externas y de la calidad "
                "de la búsqueda. Si el tema entra ambiguo, el resultado "
                "también puede salirlo."
            ),
            (
                "Un draft útil sigue necesitando tu revisión. Y si una búsqueda "
                "es débil, prefiero decirlo antes que fingir relevancia."
            ),
            "",
            "<b>Cómo usarme</b>",
            (
                "Puedes tratarme como operador, no solo como bot de comandos. "
                "Sirven cosas como:"
            ),
            "• signals membrane filtration",
            "• papers dengue surveillance",
            "• github_insights",
            "• weekly",
            "• hazme un plan del primero",
            "• apruébalo",
            "• draft",
            "",
            (
                "Pero también puedes usarme así:"
            ),
            "• quiero entender si esto da para una note o un MVP",
            "• cruza esta idea con lo que estamos construyendo en GitHub",
            "• busca señales sobre este tema y dime qué harías tú",
            "• ayúdame a convertir esta intuición en una línea de trabajo",
            "",
            "<b>Cómo sacarme más valor</b>",
            (
                "Funciono mejor cuando me das temas concretos, tensiones reales "
                "o piezas que valga la pena conectar: una observación, una nota "
                "de clase, un repo, un paper, una noticia, una sospecha."
            ),
            (
                "Si quieres rendimiento alto, no me uses solo para buscar. Úsame para "
                "sintetizar: mundo exterior + trabajo propio + identidad de Velveteen."
            ),
            "",
            "<b>Mi recomendación</b>",
            (
                "No empieces por el draft. Empieza por una línea de "
                "investigación o por una señal que de verdad te intrigue. "
                "Yo te ayudo a ver si eso debe vivir como note, post, "
                "archive o MVP."
            ),
            "",
            "<b>Lo que pienso de Velveteen</b>",
            (
                "Velveteen es más interesante cuando no separa teoría, software "
                "y voz. Su fuerza no está en parecer grande, sino en hacer "
                "visible una forma de pensar: rigurosa, aplicada, técnica y "
                "usable. Mi trabajo es ayudarte a hacer esa amalgama sin "
                "perder precisión."
            ),
            "",
            (
                "Si quieres, empezamos por aquí: <code>weekly</code>, "
                "<code>signals climate risk</code> o simplemente una idea tuya "
                "en lenguaje natural."
            ),
        ]
    )


def format_gratitude() -> str:
    return "Cuando quieras seguimos."


def format_soft_unknown(text: str) -> str:
    return "\n".join(
        [
            f"No entendí eso: <code>{escape_text(compact_text(text, 120))}</code>",
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
        return (
            f"Encontré {count} señales con buena convergencia. "
            "La mejor sí justifica explorar un MVP pequeño."
        )
    if action == RecommendedAction.NOTE and top_score >= _SOLID_SIGNAL_THRESHOLD:
        return f"{count} señales. La más fuerte da para una nota técnica."
    if action == RecommendedAction.POST:
        return f"{count} señales. Hay ángulo para un post conciso."
    if top_score < _WEAK_SIGNAL_THRESHOLD:
        return (
            "No encontré coincidencias sólidas para esta búsqueda. "
            "Te dejo resultados marginales por si quieres inspeccionarlos."
        )
    return (
        "La búsqueda devolvió algo, pero la base sigue floja. "
        "No lo tomaría todavía como señal fuerte."
    )


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
    if top_score < _WEAK_SIGNAL_THRESHOLD:
        return "Mi lectura: por ahora no la usaría como base editorial."
    return "Mi lectura: todavía la trataría con mucha cautela."


def _signal_link(title: str, url: str | None) -> str:
    label = escape_text(compact_text(title, 160))
    if not url:
        return f"<b>{label}</b>"
    return f'<a href="{escape_text(url)}"><b>{label}</b></a>'


def _render_signal_item(suggestion: SignalSuggestion) -> list[str]:
    id_prefix = f"#{suggestion.signal_id} " if suggestion.signal_id else ""
    source = suggestion.source_label or "fuente"
    title = _signal_link(id_prefix + suggestion.title, suggestion.url)
    why_text = _readable_text(suggestion.why_it_matters, limit=220)
    lines = [
        f"• <code>{escape_text(source)}</code> · {title}",
        f"  score {suggestion.relevance_score:.2f}",
        f"  {why_text}",
    ]
    if suggestion.url:
        lines.append(f'  ↗ <a href="{escape_text(suggestion.url)}">abrir fuente</a>')
    return lines


def format_signal_suggestions(
    heading: str,
    suggestions: list[SignalSuggestion],
    *,
    normalized_query: str = "",
) -> str:
    if not suggestions:
        return format_no_signals(heading, normalized_query)

    top_score = suggestions[0].relevance_score
    lead = _signal_lead(suggestions)
    take = _signal_take(suggestions)
    lines = [f"<b>{escape_text(heading)}</b>"]
    nq = normalized_query.strip()
    if nq:
        lines.append(f"Búsqueda usada: <code>{escape_text(nq)}</code>")
    lines.extend([f"{lead} {take}", ""])

    visible = suggestions if top_score >= _SOLID_SIGNAL_THRESHOLD else suggestions[:2]
    lines.append(
        "Lo más útil:"
        if top_score >= _SOLID_SIGNAL_THRESHOLD
        else "Resultados exploratorios:"
    )
    for suggestion in visible:
        lines.extend(_render_signal_item(suggestion))

    if top_score < _SOLID_SIGNAL_THRESHOLD:
        lines.extend(
            [
                "",
                "Qué haría ahora:",
                "• reformular la búsqueda con un término más técnico",
                "• probar papers o news por separado",
            ]
        )
        return "\n".join(lines)

    first = visible[0]
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
        "No encontré coincidencias útiles para este tema.",
    ]
    nq = normalized_query.strip()
    if nq:
        lines.append(f"Probé esta búsqueda: <code>{escape_text(nq)}</code>")
    lines.extend(
        [
            "Qué intentaría ahora:",
            "• un término más específico",
            "• papers X o news X por separado",
        ]
    )
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
        lines.append(f"• {escape_text(prefix + compact_text(signal.title, 140))}")
    lines.extend(
        [
            "",
            f"Editorial: <code>{escape_text(action_label)}</code>"
            f" — {_readable_text(summary.editorial_angle, limit=180)}",
            f"MVP: <code>{escape_text(summary.mvp_action.value)}</code>"
            f" — {_readable_text(summary.mvp_summary, limit=180)}",
            f"Próximo: {_readable_text(summary.next_step, limit=180)}",
            "",
        ]
    )
    first_id = next(
        (s.signal_id for s in summary.top_signals if s.signal_id is not None), None
    )
    if first_id is not None:
        lines.append(
            f"Para continuar: <code>plan {first_id}</code> o <code>weekly</code>"
        )
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
        _readable_text(idea.thesis, limit=220),
        _readable_text(idea.why_it_matters, limit=220),
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
            f"<code>{escape_text(compact_text(text, 220))}</code>",
            "",
            "Rutas posibles:",
            "• <code>signals</code> sobre este tema",
            "• <code>papers</code> sobre esto",
            "• <code>qué sigue</code>",
        ]
    )


def _plan_action_label(plan: PersistedEditorialPlan) -> str:
    return _action_label(RecommendedAction(plan.proposal.recommended_action.value))


def _plan_next_hint(plan: PersistedEditorialPlan) -> str:
    if plan.status == EditorialPlanStatus.DRAFT:
        return f"<code>apruébalo</code>  o  <code>discard_plan {plan.plan_id}</code>"
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
    why_text = _readable_text(proposal.why_it_matters, limit=260)
    lines = [
        f"<b>{escape_text(header)}</b>",
        f"<code>{escape_text(plan.status.value)}</code> · "
        f"{action_str} · confianza {proposal.confidence:.2f}",
        f"Señales: <code>{escape_text(signal_text)}</code>",
        "",
        why_text,
        "",
        f"Ángulo: {_readable_text(proposal.angle, limit=200)}",
        "",
        _plan_next_hint(plan),
    ]
    return "\n".join(lines)


def format_draft_short_version(draft: PersistedEditorialDraft) -> str:
    content = draft.draft.content
    return "\n".join(
        [
            f"<b>Draft #{draft.draft_id} — versión corta</b>",
            f"<i>{_readable_text(content.working_title, limit=180)}</i>",
            "",
            _readable_text(content.short_version, limit=500),
            "",
            f"CTA: {_readable_text(content.cta, limit=180)}",
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
        f"<i>{_readable_text(content.working_title, limit=180)}</i>",
        _readable_text(content.short_version, limit=320),
        "",
        f"CTA: {_readable_text(content.cta, limit=180)}",
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
        _readable_text(pack.thesis, limit=220),
        _readable_text(pack.scope_summary, limit=260),
        "",
        f"Builder: <code>{escape_text(pack.builder_target)}</code>",
        f"Auditor: <code>{escape_text(pack.auditor_target)}</code>",
        "",
        "Siguiente: copia el builder prompt al modelo que vayas a usar.",
    ]
    return "\n".join(lines)
