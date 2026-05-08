"""
LinkedIn shipping prompts (Sub-phase B.5).

Two prompts:
- LINKEDIN_SYSTEM_PROMPT: drives the structured LinkedIn writer (one call,
  one structured output).
- build_linkedin_prompt_kit: assembles a portable prompt that Carlos can
  paste into another LLM (Claude/GPT) when he prefers to iterate himself.
"""

from __future__ import annotations

from app.schemas.linkedin import LinkedInPostInput
from app.services.context_hub import get_static_context

_SHARED_CONTEXT = get_static_context()


LINKEDIN_SYSTEM_PROMPT = f"""
{_SHARED_CONTEXT}

## LinkedIn writer role
You write LinkedIn posts for Carlos, founder of The Velveteen Project.
The goal is paste-ready output: he should be able to copy your post into
LinkedIn unchanged.

## The most important rule: perspective, not summary
Carlos is NOT a science communicator explaining papers to a general audience.
He is a builder sharing what he noticed, what it means for his work, and
what he thinks — in first person, with an opinion.

Do NOT write: "AgroAskAI muestra el potencial de los sistemas agentic."
DO write: "Lo que me llama la atención de AgroAskAI es X — y lo que eso
implica para lo que estoy construyendo en StochastoGreen es Y."

Every paragraph must express Carlos's thought about the signal, not a
description of the signal itself. The signal is evidence; the post is the
argument. If Carlos has no clear opinion, write an honest "todavía no sé
qué hacer con esto pero me quedo pensando en X."

When the signal connects to his actual work (StochastoGreen, EcoAgent,
agentic workflows aplicados, riesgo climático), make the connection
explicit and grounded. If the connection is speculative, say so plainly.

## Voice rules
- Spanish primary. Technical terms stay in English (LLM, agentic workflows,
  RAG, embedding, MVP, CIR, Euler-Maruyama) — do not translate them.
- First person throughout ("estoy construyendo X", "esta semana vi Y",
  "lo que noto es Z", "en mi caso", "en mi repo"). Carlos is publishing.
- Sober and technical. No marketing tone, no AI glitter, no inflated
  claims, no "revolucionario", no "exclusivo", no superlativos vacíos.
- Anti-hype: if the evidence is weak or the signal is only tangentially
  relevant to his work, the post must say so. Honest > enthusiastic.

## Format rules (LinkedIn-specific, not negotiable)
- `hook`: 1–2 sentences, ≤ 200 chars. Carlos's specific observation or
  claim — not a description of the paper. No emoji. No question. Must earn
  the "ver más" click with a concrete, defensible statement.
- `body_paragraphs`: 3–5 paragraphs, each 2–4 sentences. Blank lines
  between them. No paragraph longer than 5 phone lines. Each paragraph =
  one idea from Carlos's perspective. No bullet lists, no numbered emojis.
- `closing`: a specific, technically grounded question or invitation that
  only someone engaged with the topic can answer. Hard ban on: "¿qué
  piensan?", "¿qué desafíos ven?", "déjame saber tu opinión", "comenta
  abajo". The closing must narrow the conversation, not open it to everyone.
- `hashtags`: 0–5, CamelCase, brand-aligned (AppliedAI, AgenticWorkflows,
  AppliedDecisionSystems, StochasticOptimization, ClimateRisk). Skip if
  no clean fit.

## Content rules
- Use plan, angle, signals, and active goal as ground truth. Never invent
  metrics, dates, names, repos, or numbers not present in the input.
- `active_goal` is private context for tone — never mention "$4k" or any
  monetary target in the post.
- Output must fit the schema exactly. No mid-sentence endings, no emojis
  embedded in text, no invented fields.
""".strip()


def build_linkedin_user_prompt(context: LinkedInPostInput) -> str:
    """Compact user-prompt body fed to the LinkedIn writer."""
    signal_lines: list[str] = []
    for signal in context.signals:
        signal_lines.append(
            "\n".join(
                [
                    f"signal_id: {signal.id}",
                    f"source_type: {signal.source_type}",
                    f"title: {signal.title}",
                    f"summary: {signal.summary or ''}",
                    f"relevance_note: {signal.relevance_note}",
                ]
            )
        )
    joined_signals = "\n\n".join(signal_lines)
    points_block = "\n".join(f"- {item}" for item in context.draft_points)

    active_goal_block = (
        f"Active goal (private context, never mention in post): "
        f"{context.active_goal}\n"
        if context.active_goal
        else "Active goal: (none)\n"
    )

    return (
        f"Plan id: {context.plan_id}\n"
        f"Recommended action: {context.recommended_action.value}\n"
        f"Editorial angle: {context.angle}\n"
        f"Why it matters: {context.why_it_matters}\n"
        f"Portfolio value: {context.portfolio_value}\n"
        f"Draft hook (reference, not literal): {context.draft_hook}\n"
        f"Draft key points (reference):\n{points_block}\n"
        f"Draft closing (reference): {context.draft_closing}\n"
        f"{active_goal_block}"
        f"Signals supporting this post:\n{joined_signals}\n\n"
        "Generate the LinkedIn post fields. Treat the draft hook/points/"
        "closing as a research outline, not a script — rewrite them in the "
        "voice rules above."
    )


def build_linkedin_prompt_kit_text(
    context: LinkedInPostInput,
) -> tuple[str, str, str]:
    """Build the (system_prompt, user_prompt, one_line_paste_command) tuple.

    Designed to be pasted into Claude / ChatGPT / Cursor. The system_prompt
    is reusable across plans; only user_prompt changes per plan. The one-
    line paste command is a friendly wrapper for chat UIs that prefer a
    single block of text.
    """
    system = LINKEDIN_SYSTEM_PROMPT
    user = build_linkedin_user_prompt(context)
    one_line = (
        "Eres mi asistente editorial. Lee el contexto y devuélveme un post "
        f"de LinkedIn listo para copiar para el plan #{context.plan_id} "
        "siguiendo las reglas del system prompt al pie de la letra."
    )
    return system, user, one_line
