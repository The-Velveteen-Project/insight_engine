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
from typing import TYPE_CHECKING

from app.schemas.campaign import CampaignPlanItem, PlanItemKind
from app.schemas.commands import (
    MvpIdeaSuggestion,
    SignalSuggestion,
    WeeklySourceStats,
    WeeklySummary,
)
from app.schemas.cv import GapAnalysis, TailoredCV
from app.schemas.drafts import PersistedEditorialDraft
from app.schemas.editorial import (
    EditorialPlanStatus,
    PersistedEditorialPlan,
    RecommendedAction,
)
from app.schemas.goals import ActiveGoal
from app.schemas.jobs import JobLead, JobStatus
from app.schemas.linkedin import (
    LinkedInPost,
    LinkedInPostRecord,
    LinkedInPromptKit,
    PostStatus,
)
from app.schemas.mvp_handoff import MvpHandoffPack
from app.schemas.project import ProjectBrief
from app.services.diagnostics import DiagReport

if TYPE_CHECKING:
    from app.services.campaign import CampaignProgress
    from app.services.cv_tailor import MasterCV
    from app.services.friday_recap import RecapReport
    from app.services.job_radar import RadarResult
    from app.services.post_ledger import CadenceStatus, PublishResult

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
            "• linkedin 4",
            "• publicado https://linkedin.com/posts/...",
            "• posts",
            "• jobs realista · jobs ambicioso",
            "• brecha 12 · cv 12",
            "• diag",
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
            ("Pero también puedes usarme así:"),
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
                f"• {label}: falló — {escape_text(stat.note or 'sin detalle')}"
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
        return "Esta semana empujaría una nota técnica acotada antes que un build."
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
    extra_seen = summary.signals_evaluated and summary.signals_evaluated > len(
        summary.top_signals
    )
    lines.append("")
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
            _readable_text(
                summary.thesis_paragraph or _weekly_default_thesis(summary),
                limit=900,
            ),
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
        _readable_text(idea.thesis, limit=700),
        "",
        _readable_text(idea.why_it_matters, limit=500),
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
            (f"Puedo armar el handoff del plan #{plan_id} ahora. Responde:"),
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
    return post.assembled_body()


def format_linkedin_post(
    post: LinkedInPost,
    *,
    plan_id: int,
    llm_used: bool,
    source_urls: list[tuple[str, str | None]] | None = None,
    opinion_used: bool = False,
    post_id: int | None = None,
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
    header = f"<b>📋 LinkedIn — plan #{plan_id}</b>"
    if post_id is not None:
        header = f"<b>📋 LinkedIn — plan #{plan_id} · post #{post_id}</b>"
    lines = [
        header,
        (
            "Listo para copiar. Mantén los saltos de línea cuando lo pegues "
            "(LinkedIn los respeta como párrafos en mobile)."
        ),
        "",
        f"<pre>{escape_text(body)}</pre>",
        "",
    ]
    if source_urls:
        lines.append("<b>Fuentes del plan:</b>")
        for title, url in source_urls:
            label = escape_text(compact_text(title, 200))
            if url:
                lines.append(f'↗ <a href="{escape_text(url)}">{label}</a>')
            else:
                lines.append(f"· {label}")
        lines.append("")
    if not opinion_used:
        lines.append(
            "¿Leíste las fuentes y tienes una perspectiva propia? "
            "<code>/opinion &lt;tu perspectiva&gt;</code> "
            "y regenero el post con tu voz."
        )
        lines.append("")
    lines.extend(
        [
            f"<i>{char_count} caracteres · {source_note}</i>",
            (
                "Antes de publicar: revísalo, ajústalo y dale tu voz final. "
                "Soy bueno produciendo, no soy tu publisher."
            ),
            (
                "Cuando lo publiques, dime <code>publicado &lt;url&gt;</code> "
                "y lo registro para la cadencia semanal."
            ),
        ]
    )
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
        _readable_text(pack.thesis, limit=500),
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

        title_fmt = (
            _signal_link(f"#{i} {title}", url)
            if url
            else (f"<b>{escape_text(compact_text(title, 200))}</b>")
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


def format_diag(report: DiagReport) -> str:
    """Operator self-check. Never prints keys, only whether they exist."""
    llm_line = (
        f"✅ {escape_text(report.llm_detail)}"
        if report.llm_ok
        else f"❌ {escape_text(report.llm_detail)}"
    )
    lines = [
        "<b>Diagnóstico del operador</b>",
        f"commit: <code>{escape_text(report.commit)}</code>",
        "",
        "<b>LLM</b>",
        (
            f"modelo <code>{escape_text(report.llm_model)}</code> · "
            f"host <code>{escape_text(report.llm_base_host)}</code> · "
            f"clave {'presente' if report.llm_key_present else 'ausente'}"
        ),
        llm_line,
        "",
        "<b>Discovery</b>",
        f"fuentes: {escape_text(', '.join(report.sources) or 'ninguna')}",
        f"Exa: clave {'presente' if report.exa_key_present else 'ausente'}",
        f"feeds RSS configurados: {report.rss_feed_count}",
        (
            "Anthropic: clave presente (normaliza con Claude)"
            if report.anthropic_key_present
            else "Anthropic: sin clave (normaliza con el LLM principal)"
        ),
        "",
        "<b>Goal activo</b>",
        _readable_text(report.goal_label, limit=300)
        if report.goal_label
        else "ninguno",
    ]
    if not report.llm_ok:
        lines.extend(
            [
                "",
                (
                    "Mientras el LLM falle, planes, drafts y posts salen con "
                    "fallback determinista. Revisa OPENAI_API_KEY, "
                    "OPENAI_BASE_URL y EDITORIAL_MODEL en Railway."
                ),
            ]
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post ledger and cadence (Phase 1)
# ---------------------------------------------------------------------------


def _cadence_line(cadence: CadenceStatus) -> str:
    return (
        f"Esta semana: {cadence.published_last_7d} de "
        f"{cadence.target_per_week} posts publicados."
    )


def _post_date(record: LinkedInPostRecord) -> str:
    moment = record.published_at or record.created_at
    return moment.strftime("%Y-%m-%d")


def _post_headline(record: LinkedInPostRecord) -> str:
    source = record.hook or record.body
    return escape_text(compact_text(source, 110))


def format_published_ack(result: PublishResult, cadence: CadenceStatus) -> str:
    record = result.record
    if result.created_manual:
        first = (
            f"<b>Registrado como publicado</b> · post #{record.id} "
            "(escrito por fuera del operador)."
        )
    else:
        first = f"<b>Post #{record.id} marcado como publicado.</b>"
    lines = [first]
    if record.published_url:
        lines.append(
            f'↗ <a href="{escape_text(record.published_url)}">ver en LinkedIn</a>'
        )
    lines.extend(["", _cadence_line(cadence)])
    if cadence.on_track:
        lines.append("Cadencia cumplida. Lo que sigue es criterio, no volumen.")
    else:
        remaining = cadence.target_per_week - cadence.published_last_7d
        plural = "post" if remaining == 1 else "posts"
        lines.append(
            f"Falta {remaining} {plural} para la meta semanal. "
            "Si tienes algo en curso, <code>weekly</code> o "
            "<code>news &lt;tema&gt;</code> y lo armamos."
        )
    return "\n".join(lines)


def format_posts_list(records: list[LinkedInPostRecord], cadence: CadenceStatus) -> str:
    lines = ["<b>Posts registrados</b>", _cadence_line(cadence)]
    days = cadence.days_since_last()
    if days is not None:
        lines.append(
            "Último publicado: hoy."
            if days == 0
            else f"Último publicado hace {days} día{'s' if days != 1 else ''}."
        )
    else:
        lines.append("Todavía no hay ningún post marcado como publicado.")
    lines.append("")
    if not records:
        lines.append(
            "Ninguno todavía. Genera uno con <code>linkedin &lt;plan_id&gt;</code> "
            "o registra uno externo con <code>publicado &lt;url&gt;</code>."
        )
        return "\n".join(lines)
    status_labels = {
        PostStatus.GENERATED: "generado, sin publicar",
        PostStatus.PUBLISHED: "publicado",
        PostStatus.DISCARDED: "descartado",
    }
    for record in records:
        label = status_labels.get(record.status, record.status.value)
        plan = f" · plan #{record.plan_id}" if record.plan_id is not None else ""
        lines.append(f"• #{record.id} · {label} · {_post_date(record)}{plan}")
        lines.append(f"  {_post_headline(record)}")
        if record.published_url:
            lines.append(
                f'  ↗ <a href="{escape_text(record.published_url)}">ver en LinkedIn</a>'
            )
    return "\n".join(lines)


def format_cadence_reminder(
    cadence: CadenceStatus, *, now: datetime | None = None
) -> str:
    lines = ["<b>Cadencia de LinkedIn</b>", _cadence_line(cadence)]
    days = cadence.days_since_last(now)
    if days is None:
        lines.append("No tengo registro de ningún post publicado todavía.")
    elif days >= 1:
        lines.append(
            f"El último publicado fue hace {days} día{'s' if days != 1 else ''}."
        )
    lines.append("")
    if cadence.unpublished:
        lines.append("Tienes posts generados que no se han publicado:")
        for record in cadence.unpublished[:3]:
            lines.append(f"• #{record.id} · {_post_headline(record)}")
        lines.append(
            "Si alguno ya salió, <code>publicado &lt;url&gt;</code>. "
            "Si ninguno vale, mejor uno nuevo que un post tibio."
        )
    else:
        lines.append(
            "No hay borradores en cola. Si publicaste algo por fuera, "
            "<code>publicado &lt;url&gt;</code>. Si no, dime en qué estás "
            "trabajando esta semana y armamos el post desde ahí."
        )
    return "\n".join(lines)


def format_reset_confirmation() -> str:
    return "\n".join(
        [
            "<b>Reinicio editorial</b>",
            (
                "Esto borra señales, planes, drafts, posts registrados, "
                "seguimientos y el estado del chat, y reinicia los ids en 1. "
                "El goal activo se conserva."
            ),
            "",
            "Si es lo que quieres: <code>/reset_editorial confirmar</code>",
        ]
    )


def format_reset_done(counts: dict[str, int], *, everything: bool = False) -> str:
    labels = {
        "signals": "señales",
        "editorial_plans": "planes",
        "editorial_drafts": "drafts",
        "linkedin_posts": "posts",
        "pending_handoff_followups": "seguimientos",
        "messages": "mensajes",
        "telegram_sessions": "sesiones",
    }
    if everything:
        labels.update({"job_leads": "vacantes", "campaigns": "objetivos del mes"})
    header = (
        "<b>Todo reiniciado.</b> Los próximos ids empiezan en 1."
        if everything
        else "<b>Editorial reiniciado.</b> Los próximos ids empiezan en 1."
    )
    lines = [header]
    for table, label in labels.items():
        lines.append(f"• {label}: {counts.get(table, 0)} borrados")
    lines.append("")
    lines.append(
        "El goal sigue activo. Empezamos limpio: <code>weekly</code> cuando quieras."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Job radar and pipeline (Phase 2)
# ---------------------------------------------------------------------------

_JOB_STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.NEW: "nuevas",
    JobStatus.SAVED: "guardadas",
    JobStatus.APPLIED: "aplicadas",
    JobStatus.INTERVIEW: "en entrevista",
    JobStatus.OFFER: "con oferta",
    JobStatus.REJECTED: "rechazadas",
    JobStatus.DISMISSED: "descartadas",
}


_REMOTE_POLICY_LABELS = {
    "remote": "remoto",
    "hybrid": "híbrido",
    "onsite": "presencial",
}


def _lead_facts(lead: JobLead) -> str:
    """Salary · country · remote policy, only the parts the posting stated."""
    details = lead.details
    if details is None:
        return " · remoto" if lead.remote else ""
    parts: list[str] = []
    salary = details.salary_summary()
    if salary:
        parts.append(salary)
    if details.country:
        parts.append(details.country)
    policy = _REMOTE_POLICY_LABELS.get(details.remote_policy)
    if policy:
        parts.append(policy)
    elif lead.remote:
        parts.append("remoto")
    return (" · " + " · ".join(escape_text(p) for p in parts)) if parts else ""


def _lead_lines(lead: JobLead, *, with_note: bool = True) -> list[str]:
    company = f" · {escape_text(lead.company)}" if lead.company else ""
    flag = "⭐ " if lead.dream else ""
    lines = [
        f"• {flag}#{lead.id} · <b>{escape_text(compact_text(lead.title, 110))}</b>"
        f"{company} · fit {lead.fit_score:.2f}{_lead_facts(lead)}"
    ]
    if with_note and lead.fit_note:
        lines.append(f"  {escape_text(compact_text(lead.fit_note, 160))}")
    lines.append(f'  ↗ <a href="{escape_text(lead.url)}">ver vacante</a>')
    return lines


def format_lead_detail(lead: JobLead, *, salary_verdict: str | None) -> str:
    flag = "⭐ " if lead.dream else ""
    lines = [
        f"<b>{flag}Vacante #{lead.id} · {escape_text(lead.title)}</b>",
        (escape_text(lead.company) if lead.company else "Empresa no identificada")
        + f" · estado: {_JOB_STATUS_LABELS.get(lead.status, lead.status.value)}"
        + f" · fit {lead.fit_score:.2f}",
    ]
    if lead.fit_note:
        lines.append(escape_text(compact_text(lead.fit_note, 200)))
    details = lead.details
    if details is None:
        lines.append("")
        if lead.has_posting_text:
            lines.append(
                "Todavía no extraje salario ni requisitos de esta vacante. "
                "Vuelve a pedirla en un momento o espera al radar del lunes."
            )
        else:
            lines.append(
                "No tengo el texto de la oferta, así que no puedo decirte salario "
                "ni requisitos. Ábrela con el link."
            )
        lines.append(f'↗ <a href="{escape_text(lead.url)}">ver vacante</a>')
        return "\n".join(lines)

    lines.append("")
    if details.one_line:
        lines.append(escape_text(details.one_line))
    salary = details.salary_summary()
    lines.append(
        "Salario: "
        + (
            f"{escape_text(salary)}"
            + (f" ({escape_text(salary_verdict)})" if salary_verdict else "")
            if salary
            else "no lo dice"
        )
    )
    place = ", ".join(p for p in (details.city, details.country) if p)
    policy = _REMOTE_POLICY_LABELS.get(details.remote_policy, "no lo dice")
    lines.append(
        f"Ubicación: {escape_text(place) if place else 'no la dice'} · {policy}"
    )
    if details.location_restriction:
        lines.append(f"Restricción: {escape_text(details.location_restriction)}")
    seniority_bits = [b for b in (details.seniority, details.education) if b]
    if details.years_required is not None:
        seniority_bits.append(f"{details.years_required}+ años")
    if seniority_bits:
        lines.append("Nivel: " + escape_text(" · ".join(seniority_bits)))
    if details.must_have:
        lines.append("")
        lines.append("<b>Piden</b>")
        lines.extend(f"• {escape_text(item)}" for item in details.must_have)
    if details.nice_to_have:
        lines.append("<b>Valoran</b>")
        lines.extend(f"• {escape_text(item)}" for item in details.nice_to_have)
    lines.append("")
    lines.append(f'↗ <a href="{escape_text(lead.url)}">ver vacante</a>')
    lines.append(
        f"Mover: <code>aplicado {lead.id}</code> · "
        f"<code>estado {lead.id} guardado|descartado</code>"
    )
    return "\n".join(lines)


def format_job_radar(result: RadarResult, *, scheduled: bool = False) -> str:
    title = "<b>Radar de vacantes</b>"
    if scheduled:
        title = "<b>Radar de vacantes · semanal</b>"
    elif result.lane == "realista":
        title = "<b>Radar de vacantes · liga realista</b>"
    elif result.lane == "ambicioso":
        title = "<b>Radar de vacantes · liga ambiciosa</b>"
    lines = [title]
    fetched = sum(outcome.fetched for outcome in result.outcomes)
    failed = [outcome for outcome in result.outcomes if outcome.failed]
    if result.all_failed:
        first_error = failed[0].error or "sin detalle"
        lines.append(
            "Ninguna fuente respondió (ni las bolsas directas ni Exa). "
            f"Error: <code>{escape_text(first_error)}</code>. "
            "Revisa <code>diag</code>, la clave de Exa y la red."
        )
        return "\n".join(lines)
    lines.append(
        f"{len(result.outcomes)} fuentes · {fetched} resultados · "
        f"{len(result.new_leads)} nuevas · {result.already_known} ya conocidas · "
        f"{result.below_fit} descartadas por fit bajo."
    )
    if failed:
        names = ", ".join(escape_text(outcome.query) for outcome in failed[:3])
        lines.append(
            f"{len(failed)} fuente(s) fallaron ({names}); el resto sí respondió."
        )
    lines.append("")
    if not result.new_leads:
        lines.append(
            "Nada nuevo que valga la pena esta vez. Si quieres afinar, "
            "<code>jobs &lt;tema&gt;</code> con un rol concreto."
        )
        if result.below_fit_samples:
            lines.append("")
            lines.append("Lo mejor de lo que descarté, para que juzgues el filtro:")
            for candidate in result.below_fit_samples:
                company = (
                    f" · {escape_text(candidate.company)}" if candidate.company else ""
                )
                lines.append(
                    f"• {escape_text(compact_text(candidate.title, 100))}{company} · "
                    f"fit {candidate.fit_score:.2f}"
                )
                lines.append(
                    f"  {escape_text(compact_text(candidate.fit_note, 140))} · "
                    f'<a href="{escape_text(candidate.url)}">ver</a>'
                )
        return "\n".join(lines)
    realistic = result.realistic
    ambitious = result.ambitious
    if result.lane != "ambicioso":
        lines.append("<b>Liga realista · aplica esta semana</b>")
        if realistic:
            for lead in realistic[:6]:
                lines.extend(_lead_lines(lead))
            if len(realistic) > 6:
                lines.append(f"y {len(realistic) - 6} más en <code>pipeline</code>.")
        else:
            lines.append("Nada nuevo en esta liga esta vez.")
        lines.append("")
    if result.lane != "realista":
        lines.append("<b>Liga ambiciosa · una aplicación al mes, bien preparada</b>")
        if ambitious:
            for lead in ambitious[:4]:
                lines.extend(_lead_lines(lead, with_note=False))
            if len(ambitious) > 4:
                lines.append(f"y {len(ambitious) - 4} más en <code>pipeline</code>.")
        else:
            lines.append("Nada nuevo en las bolsas directas esta vez.")
        lines.append("")
    lines.append(
        "Para mover una: <code>aplicado &lt;id&gt;</code>, "
        "<code>estado &lt;id&gt; guardado|descartado</code>. "
        "Solo una liga: <code>jobs realista</code> o <code>jobs ambicioso</code>."
    )
    return "\n".join(lines)


def format_pipeline(grouped: dict[JobStatus, list[JobLead]]) -> str:
    lines = ["<b>Pipeline de vacantes</b>"]
    total = sum(len(items) for items in grouped.values())
    if total == 0:
        lines.append(
            "Vacío. Corre <code>jobs</code> para buscar o registra una que "
            "encontraste por fuera con <code>estado</code> cuando exista."
        )
        return "\n".join(lines)
    order = (
        JobStatus.OFFER,
        JobStatus.INTERVIEW,
        JobStatus.APPLIED,
        JobStatus.SAVED,
        JobStatus.NEW,
    )
    for status in order:
        items = grouped.get(status, [])
        if not items:
            continue
        lines.append("")
        lines.append(f"<b>{_JOB_STATUS_LABELS[status].capitalize()} ({len(items)})</b>")
        for lead in items[:8]:
            lines.extend(_lead_lines(lead, with_note=status is JobStatus.NEW))
        if len(items) > 8:
            lines.append(f"  y {len(items) - 8} más.")
    return "\n".join(lines)


def format_lead_status_ack(lead: JobLead, counts: dict[str, int]) -> str:
    label = _JOB_STATUS_LABELS.get(lead.status, lead.status.value)
    lines = [
        f"<b>Vacante #{lead.id} → {label}.</b>",
        f"{escape_text(compact_text(lead.title, 120))}"
        + (f" · {escape_text(lead.company)}" if lead.company else ""),
    ]
    if lead.notes:
        lines.append(f"Nota: {escape_text(compact_text(lead.notes, 200))}")
    applied = counts.get(JobStatus.APPLIED.value, 0)
    interview = counts.get(JobStatus.INTERVIEW.value, 0)
    offer = counts.get(JobStatus.OFFER.value, 0)
    lines.append("")
    lines.append(
        f"Pipeline: {applied} aplicadas · {interview} en entrevista · "
        f"{offer} con oferta."
    )
    if lead.status is JobStatus.APPLIED:
        lines.append(
            "Si quieres, armamos un post esta semana que respalde esa aplicación: "
            "<code>github_insights</code> o <code>weekly</code>."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Master CV, gap analysis, tailored CV (Phase 2.6)
# ---------------------------------------------------------------------------

_VERDICT_LABELS = {
    "apply_now": "aplica ya",
    "apply_with_tailoring": "aplica, con el CV reordenado",
    "stretch": "es un estiramiento: aplica solo con un ángulo claro",
    "skip": "no aplicaría",
}
_STRENGTH_MARKS = {"strong": "✅", "partial": "🟡", "weak": "⚪"}


def format_cv_master_missing() -> str:
    return "\n".join(
        [
            "<b>No tengo tu CV maestro.</b>",
            (
                "Mándame el archivo <code>cv_master.md</code> como documento "
                "en este chat, con el caption <code>cv_master</code>. Lo guardo "
                "fuera del repo y desde ahí armo brechas y CVs a la medida."
            ),
        ]
    )


def format_cv_master_status(master: MasterCV, *, just_saved: bool) -> str:
    words = len(master.text.split())
    when = master.updated_at.strftime("%Y-%m-%d") if master.updated_at else "sin fecha"
    source = "base de datos" if master.source == "db" else "archivo local"
    lines = [
        "<b>CV maestro guardado.</b>" if just_saved else "<b>CV maestro</b>",
        f"{words} palabras · fuente: {source} · actualizado {when}",
    ]
    if master.dropped_verify_lines:
        lines.append(
            f"Ignoro {master.dropped_verify_lines} línea(s) marcadas [VERIFY] "
            "hasta que las confirmes."
        )
    lines.append("")
    lines.append(
        "Con esto: <code>brecha &lt;id&gt;</code> compara tu perfil con una vacante, "
        "<code>cv &lt;id&gt;</code> arma el CV a la medida."
    )
    return "\n".join(lines)


def format_gap_analysis(lead: JobLead, gap: GapAnalysis) -> str:
    verdict = _VERDICT_LABELS.get(gap.verdict, gap.verdict)
    company = f" · {escape_text(lead.company)}" if lead.company else ""
    lines = [
        f"<b>Brecha · vacante #{lead.id} · {escape_text(compact_text(lead.title, 90))}"
        f"{company}</b>",
        f"<b>Veredicto:</b> {escape_text(verdict)}",
        escape_text(gap.verdict_reason),
        "",
    ]
    if gap.covered:
        lines.append("<b>Lo que cubres</b>")
        for item in gap.covered:
            mark = _STRENGTH_MARKS.get(item.strength, "•")
            # Schema-bounded already; clipping here cut evidence mid-sentence.
            lines.append(
                f"{mark} {escape_text(item.requirement)}: {escape_text(item.evidence)}"
            )
        lines.append("")
    if gap.missing:
        lines.append("<b>Lo que no cubres</b>")
        lines.extend(
            f"❌ {escape_text(compact_text(item, 160))}" for item in gap.missing
        )
        lines.append("")
    lines.append("<b>Qué poner primero</b>")
    lines.extend(f"• {escape_text(item)}" for item in gap.foreground)
    if gap.keywords_to_mirror:
        lines.append("")
        lines.append(
            "Frases de la oferta que sí son ciertas de ti y conviene repetir: "
            + escape_text(", ".join(gap.keywords_to_mirror))
        )
    lines.append("")
    lines.append("<b>Apertura sugerida</b>")
    lines.append(f"<pre>{escape_text(gap.opener)}</pre>")
    lines.append("")
    lines.append(
        f"Siguiente: <code>cv {lead.id}</code> para el CV a la medida, "
        f"<code>aplicado {lead.id}</code> cuando envíes."
    )
    return "\n".join(lines)


def format_cv_caption(lead: JobLead) -> str:
    company = f" · {escape_text(lead.company)}" if lead.company else ""
    title = escape_text(compact_text(lead.title, 80))
    return f"CV a la medida · vacante #{lead.id} · {title}{company}"


def format_cv_delivered(
    lead: JobLead,
    cv: TailoredCV,
    markdown: str,
    *,
    gap: GapAnalysis | None,
    sent_as_file: bool,
) -> str:
    company = f" · {escape_text(lead.company)}" if lead.company else ""
    lines = [
        f"<b>CV a la medida · vacante #{lead.id} · "
        f"{escape_text(compact_text(lead.title, 80))}{company}</b>",
        f"Titular: {escape_text(cv.headline)}",
    ]
    if gap is not None:
        lines.append(
            "Veredicto de brecha: "
            + escape_text(_VERDICT_LABELS.get(gap.verdict, gap.verdict))
        )
    lines.append("")
    lines.append("<b>Notas de ajuste (para ti, no para ellos)</b>")
    lines.append(escape_text(cv.tailoring_notes))
    lines.append("")
    if sent_as_file:
        lines.append(
            "El CV completo va como archivo Markdown en este chat. Revísalo, "
            "corrige lo que no suene a ti y conviértelo a PDF antes de enviarlo."
        )
    else:
        lines.append("No pude adjuntar el archivo; va el CV completo aquí:")
        lines.append(f"<pre>{escape_text(markdown)}</pre>")
    lines.append("")
    lines.append(
        "Regla de oro: todo lo que dice sale de tu CV maestro. Si ves algo que "
        "no es cierto, el error está en el maestro; corrígelo ahí."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Monthly campaign and Friday recap (Phase 3)
# ---------------------------------------------------------------------------

_KIND_LABELS: dict[PlanItemKind, str] = {
    PlanItemKind.BUILD: "build",
    PlanItemKind.POST: "post",
    PlanItemKind.LEARN: "estudio",
    PlanItemKind.APPLY: "aplicación",
}


def _campaign_header(progress: CampaignProgress) -> str:
    campaign = progress.campaign
    company = f" · {escape_text(campaign.company)}" if campaign.company else ""
    return (
        f"<b>Objetivo del mes · vacante #{campaign.lead_id} · "
        f"{escape_text(compact_text(campaign.lead_title, 80))}{company}</b>"
    )


def _plan_item_line(item: CampaignPlanItem) -> str:
    mark = "✅" if item.done else "⬜"
    kind = _KIND_LABELS.get(item.kind, item.kind.value)
    line = f"{mark} {item.n}. [{kind} · semana {item.week}] {escape_text(item.title)}"
    if item.evidence_url:
        line += f' · <a href="{escape_text(item.evidence_url)}">evidencia</a>'
    return line


def format_no_campaign() -> str:
    return "\n".join(
        [
            "<b>No hay objetivo del mes.</b>",
            (
                "Elige una vacante ambiciosa y dime <code>objetivo &lt;id&gt;</code>: "
                "hago la brecha, la convierto en un plan de cuatro semanas con builds "
                "y posts, y el viernes te digo cómo va."
            ),
        ]
    )


def format_campaign(progress: CampaignProgress) -> str:
    campaign = progress.campaign
    lines = [
        _campaign_header(progress),
        (
            f"Semana {progress.week_number} de 4 · {progress.done}/{progress.total} "
            f"piezas · aplicar el {campaign.target_apply_at.strftime('%Y-%m-%d')} "
            f"({progress.days_left} días)"
        ),
        "",
        f"<i>{escape_text(campaign.plan.thesis)}</i>",
        "",
    ]
    for item in campaign.plan.items:
        lines.append(_plan_item_line(item))
        lines.append(f"   {escape_text(item.why)}")
    lines.append("")
    if progress.next_items:
        nxt = progress.next_items[0]
        lines.append(f"Siguiente: {nxt.n}. {escape_text(nxt.title)}")
    lines.append(
        "Marca una pieza: <code>hecho &lt;n&gt; &lt;url&gt;</code>. "
        "Cerrar sin aplicar: <code>abandonar objetivo</code>."
    )
    return "\n".join(lines)


def format_campaign_started(progress: CampaignProgress, gap: GapAnalysis) -> str:
    verdict = _VERDICT_LABELS.get(gap.verdict, gap.verdict)
    lines = [
        "<b>Objetivo del mes fijado.</b>",
        f"Brecha hoy: {escape_text(verdict)}.",
    ]
    if gap.missing:
        lines.append("Lo que el mes tiene que cerrar:")
        lines.extend(
            f"❌ {escape_text(compact_text(item, 160))}" for item in gap.missing[:6]
        )
    lines.append("")
    lines.append(format_campaign(progress))
    return "\n".join(lines)


def format_campaign_item_done(
    progress: CampaignProgress, item: CampaignPlanItem
) -> str:
    campaign = progress.campaign
    lines = [
        f"<b>Pieza {item.n} cerrada:</b> {escape_text(item.title)}",
        f"{progress.done}/{progress.total} piezas · "
        f"{progress.days_left} días para aplicar.",
    ]
    if item.kind is PlanItemKind.APPLY:
        lines.append("")
        lines.append(
            "Objetivo del mes cumplido: aplicaste con la brecha trabajada. "
            f"Registra la vacante: <code>aplicado {campaign.lead_id}</code>."
        )
        return "\n".join(lines)
    if item.kind is PlanItemKind.BUILD:
        lines.append(
            "Un build sin post es evidencia invisible. Si el plan tiene el post, "
            "es el siguiente; si no, <code>github_insights</code> y lo armamos."
        )
    if progress.next_items:
        nxt = progress.next_items[0]
        lines.append(f"Siguiente: {nxt.n}. {escape_text(nxt.title)}")
    if not progress.pending_left:
        lines.append(
            "Solo queda aplicar: <code>brecha</code> y <code>cv</code> de cierre."
        )
    return "\n".join(lines)


def format_friday_recap(report: RecapReport, *, scheduled: bool = False) -> str:
    title = "<b>Recap de la semana</b>" if not scheduled else "<b>Recap de viernes</b>"
    window = f"{report.since.strftime('%d %b')} – {report.now.strftime('%d %b')}"
    lines = [title, escape_text(window), ""]

    lines.append(f"<b>Posts</b> · {len(report.posts)} de {report.cadence_target}")
    for post in report.posts[:3]:
        headline = escape_text(compact_text(post.hook or post.body, 90))
        link = (
            f' · <a href="{escape_text(post.published_url)}">ver</a>'
            if post.published_url
            else ""
        )
        lines.append(f"• {headline}{link}")
    lines.append("")

    realistic = [lead for lead in report.applied if not lead.dream]
    dream = [lead for lead in report.applied if lead.dream]
    lines.append(
        f"<b>Aplicaciones</b> · {len(realistic)} realistas · {len(dream)} ambiciosas"
    )
    for lead in report.applied[:4]:
        company = f" · {escape_text(lead.company)}" if lead.company else ""
        lines.append(
            f"• #{lead.id} {escape_text(compact_text(lead.title, 70))}{company}"
        )
    for lead in report.moved[:4]:
        label = _JOB_STATUS_LABELS.get(lead.status, lead.status.value)
        lines.append(
            f"• #{lead.id} pasó a {label}: {escape_text(compact_text(lead.title, 60))}"
        )
    lines.append("")

    lines.append(
        f"<b>Radar</b> · {report.new_realistic} realistas nuevas · "
        f"{report.new_dream} ambiciosas nuevas esta semana"
    )
    lines.append("")

    if report.campaign is not None:
        progress = report.campaign
        lines.append(
            f"<b>Objetivo del mes</b> · semana {progress.week_number} de 4 · "
            f"{progress.done}/{progress.total} piezas · {progress.days_left} días"
        )
        for item in progress.done_this_week[:3]:
            lines.append(f"✅ {escape_text(item.title)}")
        for item in progress.next_items[:2]:
            lines.append(f"⬜ {item.n}. {escape_text(item.title)}")
        lines.append("")
    else:
        lines.append(
            "<b>Objetivo del mes</b> · ninguno. <code>objetivo &lt;id&gt;</code>"
        )
        lines.append("")

    if report.commits_by_repo:
        parts = [
            f"{escape_text(repo.split('/')[-1])} {count}"
            for repo, count in report.commits_by_repo.items()
        ]
        lines.append("<b>Repos</b> · commits: " + " · ".join(parts))
        lines.append("")

    lines.append(f"<b>Lectura honesta</b> · {report.score:.1f}/3")
    lines.append(escape_text(report.verdict))
    lines.append(escape_text("; ".join(report.reasons) + "."))
    return "\n".join(lines)


def format_reset_jobs_confirmation() -> str:
    return "\n".join(
        [
            "<b>Reinicio de vacantes</b>",
            (
                "Esto borra todas las vacantes guardadas, su pipeline y el objetivo "
                "del mes, y reinicia los ids en 1. El goal y el CV maestro se "
                "conservan. Lo editorial (señales, planes, posts) no se toca."
            ),
            "",
            "Si es lo que quieres: <code>/reset_vacantes confirmar</code>",
            "Para borrar también lo editorial: <code>/reset_todo confirmar</code>",
        ]
    )


def format_reset_all_confirmation() -> str:
    return "\n".join(
        [
            "<b>Reinicio total</b>",
            (
                "Esto borra señales, planes, drafts, posts registrados, vacantes, "
                "pipeline y objetivo del mes, y reinicia todos los ids en 1. "
                "Se conservan el goal activo y el CV maestro."
            ),
            "",
            "Si es lo que quieres: <code>/reset_todo confirmar</code>",
        ]
    )


def format_reset_jobs_done(counts: dict[str, int]) -> str:
    return "\n".join(
        [
            "<b>Vacantes reiniciadas.</b> La próxima vacante será la #1.",
            f"• vacantes: {counts.get('job_leads', 0)} borradas",
            f"• objetivos del mes: {counts.get('campaigns', 0)} borrados",
            "",
            "Cuando quieras: <code>jobs realista</code> o <code>jobs ambicioso</code>.",
        ]
    )


def format_league(lane: str, leads: list[JobLead]) -> str:
    label = "realista" if lane == "realista" else "ambiciosa"
    lines = [f"<b>Liga {label} · vacantes nuevas guardadas</b>"]
    if not leads:
        lines.append(
            f"No hay vacantes nuevas en esta liga. Busca con <code>jobs {lane}</code>."
        )
        return "\n".join(lines)
    lines.append(f"{len(leads)} mejores por fit. Las que ya moviste no aparecen.")
    lines.append("")
    for lead in leads:
        lines.extend(_lead_lines(lead, with_note=False))
    lines.append("")
    lines.append(
        "Antes de aplicar: <code>brecha &lt;id&gt;</code>. "
        "Para limpiar: <code>estado &lt;id&gt; descartado</code>."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tuesday column, Thursday finding, project brief (Phase 3.5)
# ---------------------------------------------------------------------------


def append_slot_instructions(formatted: str, *, slot: str) -> str:
    label = "columna" if slot == "columna" else "hallazgo"
    lines = [
        formatted,
        "",
        (
            f"Para convertir una en post con tu voz: <code>{label} 1: tu opinión "
            "en dos frases</code>. Hago plan, aprobación y post de LinkedIn en un "
            "solo paso. Sin opinión también funciona, pero sale más plano."
        ),
    ]
    return "\n".join(lines)


def format_project_brief_delivered(
    item: CampaignPlanItem,
    brief: ProjectBrief,
    markdown: str,
    *,
    sent_as_file: bool,
) -> str:
    lines = [
        f"<b>Brief para Claude · pieza {item.n}</b>",
        escape_text(brief.title),
        "",
        escape_text(compact_text(brief.objective, 400)),
        "",
        f"<b>Cierra:</b> {escape_text(compact_text(brief.closes_gap, 240))}",
        f"<b>Etapas:</b> {len(brief.stages)} · "
        + " → ".join(
            escape_text(compact_text(stage.name, 40)) for stage in brief.stages
        ),
    ]
    if brief.inputs_needed:
        lines.append("")
        lines.append("<b>Antes de arrancar, Claude necesita:</b>")
        lines.extend(f"• {escape_text(x)}" for x in brief.inputs_needed[:6])
    lines.append("")
    if sent_as_file:
        lines.append(
            "El brief completo va como archivo: etapas con instrucciones de "
            "investigación profunda, entregables, criterios de aceptación y el "
            "prompt de arranque. Pégalo en Claude Code o en un Claude Project junto "
            "con tu CV y el texto de la brecha."
        )
    else:
        lines.append(f"<pre>{escape_text(markdown)}</pre>")
    lines.append(f"Cuando termines: <code>hecho {item.n} &lt;url del repo&gt;</code>.")
    return "\n".join(lines)
