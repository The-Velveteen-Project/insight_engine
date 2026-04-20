"""
Helpers to format compact Telegram responses with HTML escaping.
"""

from __future__ import annotations

from html import escape

from app.schemas.commands import MvpIdeaSuggestion, SignalSuggestion, WeeklySummary
from app.schemas.drafts import PersistedEditorialDraft
from app.schemas.editorial import EditorialPlanStatus, PersistedEditorialPlan


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
            "<b>Velveteen commands</b>",
            "/papers &lt;topic&gt; — papers from arXiv",
            "/news &lt;topic&gt; — news and article signals",
            "/signals &lt;topic&gt; — mixed external signals",
            "/github_insights — repo portfolio signals",
            "/plan &lt;signal_id&gt; — create a plan from one signal",
            "/approve &lt;plan_id&gt; — approve a plan",
            "/discard_plan &lt;plan_id&gt; — discard a plan",
            "/draft &lt;plan_id&gt; — create a draft from an approved plan",
            "/show_plan &lt;plan_id&gt; — show a compact plan summary",
            "/show_draft &lt;draft_id&gt; — show a compact draft summary",
            "/weekly — weekly summary and next step",
            "/mvp_ideas &lt;topic&gt; — conservative MVP suggestions",
            "/help — this guide",
        ]
    )


def format_signal_suggestions(
    heading: str,
    suggestions: list[SignalSuggestion],
) -> str:
    if not suggestions:
        return f"<b>{escape_text(heading)}</b>\nNo useful signals found."

    lines = [f"<b>{escape_text(heading)}</b>"]
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
    return "\n".join(lines)


def format_weekly_summary(summary: WeeklySummary) -> str:
    lines = [
        "<b>Weekly summary</b>",
        f"focus: <code>{escape_text(summary.query)}</code>",
        "signals:",
    ]
    for signal in summary.top_signals:
        signal_prefix = f"#{signal.signal_id} " if signal.signal_id else ""
        lines.append(
            f"• {escape_text(signal_prefix + compact_text(signal.title, 70))}"
        )
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
    return "\n".join(lines)


def _plan_next_step(plan: PersistedEditorialPlan) -> str:
    if plan.status == EditorialPlanStatus.DRAFT:
        return f"/approve {plan.plan_id} or /discard_plan {plan.plan_id}"
    if plan.status == EditorialPlanStatus.APPROVED:
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
    return "\n".join(lines)


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
    ]
    return "\n".join(lines)
