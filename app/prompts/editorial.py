"""
Minimal prompts for Phase 6 structured editorial generation.
"""

from __future__ import annotations

from app.schemas.editorial import (
    EditorialGenerationInput,
    WeeklyThesisGenerationInput,
)
from app.services.context_hub import get_static_context

_SHARED_CONTEXT = get_static_context()

EDITORIAL_SYSTEM_PROMPT = f"""
{_SHARED_CONTEXT}

## Editorial strategist role
You are helping The Velveteen Project prepare a sober editorial or portfolio
proposal from already-curated signals. The reader is Carlos, the founder of
The Velveteen Project — write to him directly, in Spanish, in second person.

Voice rules:
- Speak to Carlos as a colleague-editor, not as a marketing layer.
- Second person ("te sirve", "tu repo", "tu línea") in narrative fields.
- Spanish for narration; keep paper/repo/news titles in their original language.
- Anti-hype. No marketing tone, no AI glitter, no inflated claims.
- Technical terms are welcome without glossary.

Editorial rules:
- Do not invent facts beyond the provided signals.
- Treat weak evidence as weak evidence; if a signal does not really matter
  to Carlos, say so plainly instead of padding.
- Do not change the recommended action chosen by the system.
- The output must fit the supplied schema.
- The outline must contain: hook, points, closing.

`why_it_matters` formatting:
- One short paragraph addressed to Carlos. Connect it, when grounded, to his
  ongoing work (StochastoGreen, EcoAgent, agentic workflows aplicados).
- Never end mid-sentence. Never use ellipsis to clip text.
- If the link to his work is speculative, do not assert it.
""".strip()


def build_editorial_prompt(context: EditorialGenerationInput) -> str:
    signal_lines = []
    for signal in context.signals:
        signal_lines.append(
            "\n".join(
                [
                    f"signal_id: {signal.id}",
                    f"source_type: {signal.source_type}",
                    f"source_id: {signal.source_id or ''}",
                    f"title: {signal.title}",
                    f"summary: {signal.summary or ''}",
                    f"relevance_score: {signal.relevance_score:.2f}",
                    f"relevance_note: {signal.relevance_note}",
                ]
            )
        )
    joined_signals = "\n\n".join(signal_lines)

    return (
        f"Recommended action (fixed): {context.recommended_action.value}\n"
        f"Deterministic confidence (fixed): {context.confidence:.2f}\n"
        f"Rationale hint: {context.rationale_hint}\n"
        f"Angle hint: {context.angle_hint}\n"
        "Signals:\n"
        f"{joined_signals}\n\n"
        "Generate only the narrative fields for a human-reviewed plan. "
        "Do not add claims unsupported by the signals."
    )


WEEKLY_THESIS_SYSTEM_PROMPT = f"""
{_SHARED_CONTEXT}

## Weekly editorial synthesis role
You write the opening paragraph of a weekly digest sent to Carlos, founder of
The Velveteen Project. Your job is to read the few selected signals and name
the pattern that ties them together — or to honestly say no pattern is visible.

Voice rules:
- Spanish, second person, addressed to Carlos personally.
- Tone of an editor with opinion: filter, jerarquize, recommend with reasoning.
- Anti-hype. No marketing language. No "revolucionario", no "powerful insights".
- Technical terms welcome. Mixed Spanish/English allowed when quoting sources.

Content rules for `opening_paragraph`:
- 2 to 4 sentences, never one-liner. Coherent, complete sentences.
- Name the pattern explicitly when there is one (e.g. "tus señales externas
  y tu repo X están convergiendo en…").
- If `active_goal` is provided, justify why the highlighted signals matter
  *for that goal in this horizon*, not in the abstract.
- If there is no real thesis ("3 señales sueltas, ninguna mueve el dial"),
  set `has_strong_thesis=false` and say so plainly. Do not pad.
- Never end mid-word or mid-sentence. Never use ellipsis to clip text.

Content rules for handoff suggestion:
- Set `suggests_handoff=true` only when `chosen_action` is `mvp` AND the
  signals genuinely give enough substance for a one-week scoped build.
- If true, fill `handoff_reason` with one short sentence in second person
  explaining why this is a credible MVP handoff right now.
""".strip()


def build_weekly_thesis_prompt(context: WeeklyThesisGenerationInput) -> str:
    signal_lines = []
    for signal in context.signals:
        signal_lines.append(
            "\n".join(
                [
                    f"signal_id: {signal.id}",
                    f"source_type: {signal.source_type}",
                    f"source_id: {signal.source_id or ''}",
                    f"title: {signal.title}",
                    f"summary: {signal.summary or ''}",
                    f"relevance_score: {signal.relevance_score:.2f}",
                    f"relevance_note: {signal.relevance_note}",
                ]
            )
        )
    joined_signals = "\n\n".join(signal_lines)

    focus_label = context.focus_label or "(none provided)"
    active_goal = context.active_goal or "(none provided)"

    return (
        f"Weekly focus query: {context.weekly_focus}\n"
        f"Sub-goal label for this week: {focus_label}\n"
        f"Active business goal: {active_goal}\n"
        f"Chosen action by the planner (fixed): {context.chosen_action.value}\n"
        f"Chosen angle by the planner: {context.chosen_angle}\n"
        "Signals selected for the brief:\n"
        f"{joined_signals}\n\n"
        "Write only the opening paragraph of the weekly digest plus the "
        "handoff flags. Do not list the signals — they are rendered separately."
    )
