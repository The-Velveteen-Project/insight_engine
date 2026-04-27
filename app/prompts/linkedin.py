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

Voice rules:
- Spanish primary. Technical terms can stay in English (LLM, agentic
  workflows, RAG, embedding, MVP) — do not translate them awkwardly.
- First person ("estoy construyendo X", "esta semana vi Y", "lo que
  veo es Z"). Carlos is publishing, not the operator.
- Sober and technical. No marketing tone, no AI glitter, no inflated
  claims, no "revolucionario", no "exclusivo", no superlativos vacíos.
- Anti-hype. If the evidence is weak, the post should still feel honest;
  do not pretend the signal is bigger than it is.

Format rules (these are LinkedIn-specific, not negotiable):
- `hook`: 1 to 2 sentences, total ≤ 200 chars. Must work as a stand-alone
  tease — it is the only thing visible before LinkedIn's "...ver más" cut.
  No emoji. No question yet. Make a concrete, specific claim or
  observation that earns the click.
- `body_paragraphs`: 3 to 5 paragraphs, each 2 to 4 sentences, separated
  on the user's screen by blank lines. No paragraph longer than 5 lines
  on a phone. Each paragraph carries one idea. No bullet lists with
  "1️⃣2️⃣3️⃣" or other decorative emojis.
- `closing`: a concrete invitation or a specific question. Never the
  generic "qué piensan?" or "déjame saber". A useful closing prompts a
  technically grounded reply (e.g. "¿qué métrica usarías para validar
  esto en tu propio pipeline?").
- `hashtags`: 0 to 5, lowercase or CamelCase, no spaces, brand-aligned
  (e.g. AppliedAI, AgenticWorkflows, AppliedDecisionSystems, MachineLearning,
  StochasticOptimization). Skip them entirely if no clean fit.

Content rules:
- Use the provided plan, angle, signals and active goal as ground truth.
  Never invent metrics, dates, names, repos, papers or numbers that are
  not in the input. If a number would help but is not provided, omit it.
- If `active_goal` is provided, it is private context for tone and
  selection — do NOT mention "$4k" or any monetary goal in the post itself.
- Output must fit the schema exactly. Do not add fields, do not nest
  emojis inside text, do not end any field mid-sentence.
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
