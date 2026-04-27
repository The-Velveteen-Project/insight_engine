"""
Helpers to format readable Telegram responses with HTML escaping.

Tone principles:
- Spanish first, Spanish-speaker friendly.
- Direct and first-person where appropriate. No machine-output labels.
- Technical terms (plan, draft, archive, note, post, mvp) stay in English
  because that's how the commands work — mixing is intentional and clear.
- Readability-first: explain enough for the message to stand on its own.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from html import escape

from app.schemas.commands import (
    MvpIdeaSuggestion,
    SignalSuggestion,
    WeeklySourceStats,
    WeeklySummary,
)
from app.schemas.drafts import PersistedEditorialDraft
from app.schemas.editorial import (
    EditorialPlanStatus,
    PersistedEditorialPlan,
    RecommendedAction,
)
from app.schemas.goals import ActiveGoal
from app.schemas.linkedin import LinkedInPost, LinkedInPromptKit
from app.schemas.mvp_handoff import MvpHandoffPack

_SOLID_SIGNAL_THRESHOLD = 0.45
_WEAK_SIGNAL_THRESHOLD = 0.25


_MIN_TRIM_POSITION = 40


def compact_text(text: str, limit: int) -> str:
    """Whitespace-normalize text and clip cleanly within `limit`.

    Hard rule: never end the result with an ellipsis. If trimming is needed,
    end at the last sentence boundary within `limit`, falling back to the
    last word boundary. The degenerate single-huge-word case returns a raw
    slice without an ellipsis — still no `…` ever appended by this function.
    """
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    window = compact[:limit]
    for marker in (". ", "? ", "! ", ".\n", "?\n", "!\n"):
        idx = window.rfind(marker)
        if idx >= _MIN_TRIM_POSITION:
            return compact[: idx + 1].rstrip()
    space_idx = window.rfind(" ")
    if space_idx >= _MIN_TRIM_POSITION:
        return compact[:space_idx].rstrip(",;:—-")
    return window


def escape_text(text: str) -> str:
    return escape(text, quote=False)


def _readable_text(text: str, *, limit: int = 320) -> str:
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
            "Cómo suelo servir mejor:",
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
                "y ordenar una línea hasta plan o draft sin perder el hilo."
            ),
            "",
            "Si quieres empezar simple:",
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
            (
                "Puedo ayudarte a buscar señales, cruzarlas con GitHub, "
                "mover una línea a plan o revisar qué haría yo ahora."
            ),
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

    if mixed and top_score < 0.70:
        return "Señales mezcladas — trataría como note antes que forzar un MVP."
    if dominant == RecommendedAction.MVP and top_score >= 0.75 and not mixed:
        return "Vale la pena probar un build pequeño y acotado."
    if dominant == RecommendedAction.NOTE and not mixed:
        return "Lo más sensato es una nota técnica sobria."
    if dominant == RecommendedAction.POST:
        return "Da para un post claro, no para build todavía."
    if top_score < _WEAK_SIGNAL_THRESHOLD:
        return "Mi lectura: por ahora no la usaría como base editorial."
    return "Mi lectura: todavía la trataría con mucha cautela."


def _query_line(label: str, query: str) -> str:
    return f"{label}: <code>{escape_text(compact_text(query, 200))}</code>"


def _continuation_line(text: str) -> str:
    return f"Si quieres, yo seguiría por aquí: {text}"


def _signal_link(title: str, url: str | None) -> str:
    label = escape_text(compact_text(title, 200))
    if not url:
        return f"<b>{label}</b>"
    return f'<a href="{escape_text(url)}"><b>{label}</b></a>'


def _render_signal_item(suggestion: SignalSuggestion) -> list[str]:
    id_prefix = f"#{suggestion.signal_id} " if suggestion.signal_id else ""
    source = suggestion.source_label or "fuente"
    title = _signal_link(id_prefix + suggestion.title, suggestion.url)
    why_text = _readable_text(suggestion.why_it_matters, limit=360)
    lines = [
        f"• <code>{escape_text(source)}</code> · {title}",
        f"  Por qué te sirve: {why_text}",
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
        lines.append(_query_line("Búsqueda usada", nq))
    lines.extend([lead, take, ""])

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
                "• usar el resultado como exploración, no como base editorial todavía",
            ]
        )
        return "\n".join(lines)

    first = visible[0]
    if first.signal_id is not None:
        action_str = escape_text(_action_label(first.suggested_action))
        lines.extend(
            [
                "",
                _continuation_line(
                    f"<code>plan {first.signal_id}</code> "
                    f"si quieres convertir la señal más defendible en {action_str}"
                ),
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
        lines.append(_query_line("Probé esta búsqueda", nq))
    lines.extend(
        [
            "Qué intentaría ahora:",
            "• un término más específico",
            "• papers X o news X por separado",
            (
                "• una formulación más cercana al problema técnico real "
                "que quieres investigar"
            ),
        ]
    )
    return "\n".join(lines)


def _format_source_stats_footer(stats: list[WeeklySourceStats]) -> str:
    """Render the honest discovery footer at the bottom of the weekly.

    The shape is: one bold header line + one line per source with its
    fetched/in-brief counts and an optional explanation when nothing made
    the brief (or the source failed). Designed to be skimmable and to
    answer the operator's question "what did you actually try?".
    """
    lines = ["<b>Discovery esta semana</b>"]
    for stat in stats:
        label = escape_text(stat.source_label)
        if stat.failed:
            lines.append(
                f"• {label}: falló — "
                f"{escape_text(stat.note or 'sin detalle')}"
            )
            continue
        line = (
            f"• {label}: {stat.candidates_returned} candidatos · "
            f"{stat.candidates_in_brief} en el brief"
        )
        if stat.note:
            line += f" — {escape_text(stat.note)}"
        lines.append(line)
    return "\n".join(lines)


def _weekly_default_thesis(summary: WeeklySummary) -> str:
    """Last-resort opener used only if no thesis was generated upstream."""
    if summary.mvp_action == RecommendedAction.MVP:
        return (
            "Esta semana sí veo base para explorar una línea de MVP pequeña, "
            "anclada en lo que ya estás moviendo."
        )
    if summary.editorial_action == RecommendedAction.NOTE:
        return (
            "Esta semana empujaría una nota técnica acotada antes que un build."
        )
    if summary.editorial_action == RecommendedAction.POST:
        return "La oportunidad se ve más editorial que constructiva esta semana."
    return (
        "Semana conservadora: no veo todavía suficiente base como para "
        "empujar esta línea con criterio."
    )


def format_weekly_summary(summary: WeeklySummary) -> str:
    lines: list[str] = ["🐇 <b>Velveteen Operator — Weekly</b>"]
    if summary.active_goal:
        goal_text = _readable_text(summary.active_goal, limit=200)
        lines.append(f"<i>Goal activo: {goal_text}</i>")
    if summary.focus_label:
        lines.append(
            f"<i>Sub-foco de la semana: "
            f"{_readable_text(summary.focus_label, limit=160)}</i>"
        )
    lines.extend(
        [
            "",
            "<b>Lo que vi esta semana</b>",
            _readable_text(
                summary.thesis_paragraph or _weekly_default_thesis(summary),
                limit=900,
            ),
            "",
        ]
    )

    extra_seen = (
        summary.signals_evaluated
        and summary.signals_evaluated > len(summary.top_signals)
    )
    if extra_seen:
        lines.append(
            "<b>Señales que pasaron el filtro editorial</b> "
            f"(de {summary.signals_evaluated} vistas)"
        )
    else:
        lines.append("<b>Señales que pasaron el filtro editorial</b>")

    for signal in summary.top_signals:
        lines.extend(_render_signal_item(signal))

    lines.extend(
        [
            "",
            "<b>Mi lectura</b>",
            _readable_text(summary.editorial_angle, limit=420),
        ]
    )

    if summary.handoff_proposal:
        lines.extend(
            [
                "",
                "<b>Veo señal clara de MVP handoff</b>",
                _readable_text(summary.handoff_proposal, limit=420),
                "¿Te lo armo en cuanto apruebes el plan?",
            ]
        )

    rest = (
        max(summary.signals_evaluated - len(summary.top_signals), 0)
        if summary.signals_evaluated
        else 0
    )
    if rest:
        lines.extend(
            [
                "",
                "<b>Lo que no llegó al brief</b>",
                (
                    f"Las otras {rest} señales que entraron esta semana no pasaron "
                    "el filtro: o eran ruido recurrente, o repetían cosas previas, "
                    "o eran interesantes en abstracto pero no mueven el dial hoy."
                ),
            ]
        )

    if summary.source_stats:
        lines.extend(["", _format_source_stats_footer(summary.source_stats)])

    lines.append("")
    first_id = next(
        (s.signal_id for s in summary.top_signals if s.signal_id is not None), None
    )
    if first_id is not None:
        lines.extend(
            [
                "<b>Por dónde seguiría yo</b>",
                (
                    f"Si te alinea: <code>plan {first_id}</code> y armo el plan "
                    "agregado."
                ),
                (
                    "Si tienes algo propio en curso (notas, código, una intuición), "
                    "mándalo y te digo si veo pieza editorial ahí."
                ),
            ]
        )
    else:
        lines.append(_continuation_line("<code>weekly</code>"))
    return "\n".join(lines)


def format_mvp_idea(idea: MvpIdeaSuggestion) -> str:
    signal_text = ", ".join(str(s) for s in idea.signal_ids) or "—"
    is_mvp = idea.recommended_action == RecommendedAction.MVP
    title = "Idea de MVP" if is_mvp else "Lectura de build"
    take = (
        "Sí probaría un MVP pequeño y muy acotado."
        if is_mvp
        else "No forzaría un build todavía."
    )
    lines = [
        f"<b>{title}</b>",
        _query_line("Línea que revisé", idea.query),
        (
            "Mi decisión hoy es "
            f"<code>{escape_text(idea.recommended_action.value)}</code>. {take}"
        ),
        "",
        "<b>Mi lectura</b>",
        _readable_text(idea.thesis, limit=260),
        _readable_text(idea.why_it_matters, limit=260),
        "",
        (
            "<b>Por qué no la tomaría más grande</b>"
            if not is_mvp
            else "<b>Qué tendría que probar</b>"
        ),
        _readable_text(idea.problem, limit=240),
        "",
    ]
    if idea.supporting_signals:
        lines.append("<b>Señales que sostienen esta lectura</b>")
        for signal in idea.supporting_signals:
            lines.extend(_render_signal_item(signal))
        lines.append("")

    lines.extend(
        [
            f"Fuentes consultadas: {escape_text(', '.join(idea.possible_sources))}",
            f"Señales persistidas: <code>{signal_text}</code>",
            f"Tipo de sistema sugerido: {_readable_text(idea.system_type, limit=200)}",
            f"Encaje con Velveteen: {_readable_text(idea.portfolio_fit, limit=220)}",
        ]
    )
    if idea.signal_ids:
        lines.append("")
        if is_mvp:
            lines.append(
                _continuation_line(
                    f"<code>plan {idea.signal_ids[0]}</code> y, "
                    "si lo apruebas, luego <code>mvp_handoff</code>"
                )
            )
        else:
            lines.append(
                _continuation_line(
                    f"<code>plan {idea.signal_ids[0]}</code> "
                    "si quieres convertir esta lectura en note o post"
                )
            )
    return "\n".join(lines)


def format_note_capture_ack(text: str) -> str:
    return "\n".join(
        [
            "Registrado como nota manual.",
            f"<code>{escape_text(compact_text(text, 220))}</code>",
            "",
            "Con esto puedo ayudarte de tres formas:",
            "• buscar señales relacionadas afuera",
            "• buscar papers sobre este tema",
            "• sugerir qué haría yo ahora con esta línea",
            "",
            (
                "Prueba con: <code>signals</code> · <code>papers</code> "
                "· <code>qué sigue</code>"
            ),
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
        "<b>Por qué movería esta línea</b>",
        why_text,
        "",
        f"<b>Ángulo propuesto</b>\n{_readable_text(proposal.angle, limit=220)}",
        "",
        f"<b>Siguiente paso sugerido</b>\n{_plan_next_hint(plan)}",
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
            f"CTA sugerido: {_readable_text(content.cta, limit=180)}",
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
        "",
        "<b>Versión corta</b>",
        _readable_text(content.short_version, limit=320),
        "",
        f"CTA sugerido: {_readable_text(content.cta, limit=180)}",
        "",
        _continuation_line("<code>muéstramelo</code> para ver el cuerpo completo"),
    ]
    return "\n".join(lines)


def goal_deadline_summary(goal: ActiveGoal) -> str | None:
    if goal.deadline_at is None:
        return None
    if goal.deadline_at.tzinfo is None:
        deadline = goal.deadline_at.replace(tzinfo=UTC)
    else:
        deadline = goal.deadline_at
    now = datetime.now(tz=deadline.tzinfo)
    delta_days = (deadline - now).days
    when = deadline.date().isoformat()
    if delta_days > 1:
        return f"deadline {when} · {delta_days} días restantes"
    if delta_days == 1:
        return f"deadline {when} · queda 1 día"
    if delta_days == 0:
        return f"deadline {when} · vence hoy"
    return f"deadline {when} · vencido hace {abs(delta_days)} días"


def format_active_goal(goal: ActiveGoal) -> str:
    lines = [
        "<b>Goal activo</b>",
        _readable_text(goal.label, limit=300),
    ]
    if goal.description:
        lines.append(_readable_text(goal.description, limit=400))
    deadline_line = goal_deadline_summary(goal)
    if deadline_line is not None:
        lines.append(f"<i>{escape_text(deadline_line)}</i>")
    lines.extend(
        [
            "",
            (
                "Lo uso para filtrar discovery, anclar la tesis del weekly y "
                "decidir cuándo proponer un MVP handoff. Si quieres cambiarlo: "
                "<code>/goal &quot;...&quot; --by YYYY-MM-DD</code>. "
                "Para borrarlo: <code>/clear_goal</code>."
            ),
        ]
    )
    return "\n".join(lines)


def format_no_active_goal() -> str:
    return "\n".join(
        [
            "<b>Sin goal activo</b>",
            (
                "Hoy no estoy filtrando ni sintetizando con un objetivo "
                "concreto. Si me das uno, todo lo que vea esta semana lo "
                "evalúo a la luz de eso."
            ),
            "",
            "Por ejemplo:",
            (
                "<code>/goal &quot;cliente $4k posicionando agentic workflows "
                "aplicados&quot; --by 2026-08-01</code>"
            ),
        ]
    )


def format_goal_set(goal: ActiveGoal) -> str:
    lines = [
        "<b>Goal activo actualizado</b>",
        _readable_text(goal.label, limit=300),
    ]
    deadline_line = goal_deadline_summary(goal)
    if deadline_line is not None:
        lines.append(f"<i>{escape_text(deadline_line)}</i>")
    lines.extend(
        [
            "",
            (
                "Desde ahora lo uso para anclar el weekly, ranquear discovery "
                "y proponer handoffs cuando aparezca un plan con tracción de MVP."
            ),
        ]
    )
    return "\n".join(lines)


def format_goal_cleared(goal: ActiveGoal) -> str:
    return "\n".join(
        [
            "<b>Goal archivado</b>",
            _readable_text(goal.label, limit=300),
            "",
            (
                "Sin goal activo. El weekly y el ranking pierden el filtro de "
                "horizonte hasta que definas uno nuevo."
            ),
        ]
    )


def format_handoff_offer_after_approve(plan_id: int) -> str:
    return "\n".join(
        [
            "",
            "<b>Veo señal clara de MVP handoff aquí</b>",
            (
                f"Puedo armar el handoff del plan #{plan_id} ahora. "
                "Responde:"
            ),
            "• <code>sí</code> o <code>hazlo</code> — armo el handoff ya",
            (
                "• <code>después</code> — te pregunto en 2 días si ya "
                "empezaste o te recuerdo armarlo"
            ),
            "• <code>no, mejor draft</code> — saco el draft en su lugar",
        ]
    )


def format_handoff_postponed(plan_id: int) -> str:
    return (
        f"Anotado. En 2 días reviso si ya hay un repo apuntando al plan #{plan_id} "
        "y te aviso; si no, te recuerdo armarlo."
    )


def format_handoff_followup_with_match(
    *,
    plan_id: int,
    plan_angle: str,
    repo_full_name: str,
    rationale: str,
) -> str:
    return "\n".join(
        [
            "<b>Veo movimiento sobre el plan que dejaste pendiente</b>",
            f"Plan #{plan_id} — {_readable_text(plan_angle, limit=240)}",
            "",
            f"Tu repo <code>{escape_text(repo_full_name)}</code> apunta a esto:",
            _readable_text(rationale, limit=400),
            "",
            (
                "¿Quieres que lo escalemos a portfolio piece (handoff completo "
                "+ draft) o seguimos por libre? Responde:"
            ),
            "• <code>hazlo</code> — armo el handoff ahora",
            "• <code>olvídalo</code> — cierro el recordatorio",
        ]
    )


def format_handoff_followup_no_match(*, plan_id: int, plan_angle: str) -> str:
    return "\n".join(
        [
            "<b>Recordatorio del MVP que quedó pendiente</b>",
            (
                f"Han pasado 2 días desde que dijiste *después* al MVP del "
                f"plan #{plan_id}."
            ),
            f"<i>{_readable_text(plan_angle, limit=240)}</i>",
            "",
            "No veo todavía un repo apuntando a esto. Responde:",
            "• <code>hazlo</code> — armo el handoff ahora",
            "• <code>olvídalo</code> — cierro el recordatorio",
        ]
    )


def format_followup_dismissed() -> str:
    return (
        "Listo, cierro el recordatorio. "
        "Si después quieres retomar: <code>weekly</code>."
    )


def format_no_pending_followup() -> str:
    return (
        "No tengo recordatorios abiertos por dismissar. Si quieres ver lo que "
        "está vivo: <code>weekly</code>."
    )


def _assemble_linkedin_body(post: LinkedInPost) -> str:
    """Stitch hook + paragraphs + closing + hashtags into a paste-ready block.

    Blank lines between paragraphs are preserved so when Carlos pastes into
    LinkedIn the rendering matches what he sees in Telegram.
    """
    sections: list[str] = [post.hook.strip(), ""]
    for paragraph in post.body_paragraphs:
        sections.append(paragraph.strip())
        sections.append("")
    sections.append(post.closing.strip())
    if post.hashtags:
        tag_line = " ".join(f"#{tag.strip().lstrip('#')}" for tag in post.hashtags)
        sections.extend(["", tag_line])
    return "\n".join(sections).strip()


def format_linkedin_post(
    post: LinkedInPost,
    *,
    plan_id: int,
    llm_used: bool,
) -> str:
    body = _assemble_linkedin_body(post)
    char_count = len(body)
    source_note = (
        "Generado con LLM editorial."
        if llm_used
        else (
            "Generado con fallback determinista — el LLM no estaba disponible. "
            "Léelo como borrador, no como post listo."
        )
    )
    lines = [
        f"<b>📋 LinkedIn — plan #{plan_id}</b>",
        (
            "Listo para copiar. Mantén los saltos de línea cuando lo pegues "
            "(LinkedIn los respeta como párrafos en mobile)."
        ),
        "",
        f"<pre>{escape_text(body)}</pre>",
        "",
        f"<i>{char_count} caracteres · {source_note}</i>",
        (
            "Antes de publicar: revísalo, ajústalo y dale tu voz final. "
            "Soy bueno produciendo, no soy tu publisher."
        ),
    ]
    return "\n".join(lines)


def format_linkedin_prompt_kit(kit: LinkedInPromptKit) -> str:
    """Render the portable prompt kit so Carlos can paste it elsewhere."""
    return "\n".join(
        [
            f"<b>🧰 Prompt kit de LinkedIn — plan #{kit.plan_id}</b>",
            (
                "Pégalo en Claude / ChatGPT / Cursor cuando quieras "
                "iterar tú mismo el post."
            ),
            "",
            "<b>System prompt</b>",
            f"<pre>{escape_text(kit.system_prompt)}</pre>",
            "",
            "<b>User prompt</b>",
            f"<pre>{escape_text(kit.user_prompt)}</pre>",
            "",
            "<b>One-liner para chat UIs (úsalo como mensaje de apertura)</b>",
            f"<pre>{escape_text(kit.one_line_paste_command)}</pre>",
        ]
    )


def format_mvp_handoff_summary(pack: MvpHandoffPack) -> str:
    signal_text = ", ".join(str(s) for s in pack.signal_ids)
    lines = [
        "<b>MVP handoff listo</b>",
        f"Plan: <code>#{pack.plan_id}</code> · señales: <code>{signal_text}</code>",
        "",
        "<b>Tesis</b>",
        _readable_text(pack.thesis, limit=240),
        "",
        "<b>Scope sugerido</b>",
        _readable_text(pack.scope_summary, limit=280),
        "",
        f"Builder: <code>{escape_text(pack.builder_target)}</code>",
        f"Auditor: <code>{escape_text(pack.auditor_target)}</code>",
        "",
        _continuation_line(
            "copiar el builder prompt al modelo que vayas a usar y "
            "reservar el auditor para revisar el resultado"
        ),
    ]
    return "\n".join(lines)


def format_signal_explain(
    signals: list[dict[str, str | None]],
) -> str:
    """Detailed per-signal breakdown in response to 'de qué trata cada uno?'.

    Each entry in `signals` must have: source_label, title, summary, url.
    Summary is shown up to ~380 chars at a sentence boundary.
    """
    if not signals:
        return "No tengo señales recientes guardadas en contexto."

    lines: list[str] = [f"<b>Las {len(signals)} señales en más detalle:</b>", ""]
    for i, sig in enumerate(signals, 1):
        source = escape_text(str(sig.get("source_label") or "fuente"))
        title = str(sig.get("title") or "")
        summary = str(sig.get("summary") or "")
        url = str(sig.get("url") or "") or None

        # Trim summary to a readable length at a sentence boundary
        trimmed = compact_text(summary, 380)

        title_fmt = _signal_link(f"#{i} {title}", url) if url else (
            f"<b>{escape_text(compact_text(title, 200))}</b>"
        )
        lines.append(f"<code>{source}</code> · {title_fmt}")
        if trimmed:
            lines.append(f"  {escape_text(trimmed)}")
        if url:
            lines.append(f'  ↗ <a href="{escape_text(url)}">abrir fuente</a>')
        lines.append("")

    lines.append(
        "Si querés armar un plan sobre alguna, decime el número "
        "o usá <code>/plan &lt;id&gt;</code>."
    )
    return "\n".join(lines)
