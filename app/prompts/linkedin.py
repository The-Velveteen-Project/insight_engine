"""
LinkedIn shipping prompts (Sub-phase B.5, language-aware since Phase 2).

Two prompts:
- build_linkedin_system_prompt(language): drives the structured LinkedIn
  writer (one call, one structured output). LINKEDIN_SYSTEM_PROMPT is the
  default-language build kept for callers that want a module constant.
- build_linkedin_prompt_kit: assembles a portable prompt that Carlos can
  paste into another LLM (Claude/GPT) when he prefers to iterate himself.

Voice exemplars are Carlos's own published posts (app/context/
linkedin_voice_exemplars.md). They set the register: argument first, one
number, one defended idea, his work as the subject.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.schemas.linkedin import LinkedInPostInput
from app.services.context_hub import get_static_context

_SHARED_CONTEXT = get_static_context()
_EXEMPLARS_PATH = Path(__file__).resolve().parent.parent / "context"
_EXEMPLARS_FILE = _EXEMPLARS_PATH / "linkedin_voice_exemplars.md"


def _voice_exemplars() -> str:
    try:
        return _EXEMPLARS_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


_LANGUAGE_RULES: dict[str, str] = {
    "en": (
        "- Write in English. Carlos's audience is international and the roles "
        "he is targeting are English-speaking; his best-performing posts are "
        "in English.\n"
        "- Keep Spanish only for proper nouns and quoted material.\n"
    ),
    "es": (
        "- Write in Spanish. Technical terms stay in English (LLM, agentic "
        "workflows, RAG, embedding, MVP, CIR, Euler-Maruyama); do not "
        "translate them.\n"
    ),
}


def resolve_language(language: str | None = None) -> str:
    candidate = (language or settings.linkedin_language or "en").strip().lower()
    return candidate if candidate in _LANGUAGE_RULES else "en"


def build_linkedin_system_prompt(language: str | None = None) -> str:
    lang = resolve_language(language)
    language_rules = _LANGUAGE_RULES[lang]
    exemplars = _voice_exemplars()
    exemplar_block = f"\n\n{exemplars}\n" if exemplars else "\n"
    return f"""
{_SHARED_CONTEXT}

## LinkedIn writer role
You write LinkedIn posts for Carlos, founder of The Velveteen Project.
The goal is paste-ready output: he should be able to copy your post into
LinkedIn unchanged.

## The most important rule: perspective, not summary
Carlos is NOT a science communicator explaining papers to a general audience.
He is a builder and researcher sharing what he noticed, what it means for his
work, and what he thinks, in first person, with an opinion.

Do NOT write: "AgroAskAI shows the potential of agentic systems."
DO write: "What caught my attention in AgroAskAI is X, and what that implies
for what I'm building in StochastoGreen is Y."

Every paragraph must express Carlos's thought about the signal, not a
description of the signal itself. The signal is evidence; the post is the
argument. If Carlos has no clear opinion, write an honest "I don't know yet
what to do with this, but I keep coming back to X."

When the signal connects to his actual work (CARMEN, StochastoGreen, EcoAgent,
his bioinformatics thesis, applied agentic workflows, climate and health risk),
make the connection explicit and grounded. If the connection is speculative,
say so plainly. His work is the subject; the signal is supporting evidence.

## Language
{language_rules}
## Voice rules
- First person throughout ("I'm building X", "this week I saw Y", "what I
  notice is Z", "in my case", "in my repo"). Carlos is publishing.
- Sober and technical. No marketing tone, no AI glitter, no inflated claims,
  no "revolutionary", no "game-changing", no empty superlatives.
- Anti-hype: if the evidence is weak or the signal is only tangentially
  relevant to his work, the post must say so. Honest beats enthusiastic.
- One concrete number when the input contains one. Never invent numbers.
- Name the one thing he would defend hardest.
{exemplar_block}
## Format rules (LinkedIn-specific, not negotiable)
- `hook`: 1–2 sentences, ≤ 200 chars. Carlos's specific observation or claim,
  not a description of the paper. No emoji. No question. Must earn the
  "see more" click with a concrete, defensible statement.
- `body_paragraphs`: 3–5 paragraphs, each 2–4 sentences. Blank lines between
  them. No paragraph longer than 5 phone lines. Each paragraph = one idea
  from Carlos's perspective. No bullet lists, no numbered emojis.
- `closing`: a specific, technically grounded question or invitation that
  only someone engaged with the topic can answer. Hard ban on "what do you
  think?", "what challenges do you see?", "let me know your thoughts",
  "comment below". The closing must narrow the conversation, not open it.
- `hashtags`: 0–4, CamelCase, brand-aligned (AppliedAI, AgenticWorkflows,
  AppliedDecisionSystems, ScientificML, ClimateRisk). Skip if no clean fit.

## Content rules
- Use plan, angle, signals, and active goal as ground truth. Never invent
  metrics, dates, names, repos, or numbers not present in the input.
- `active_goal` is private context for tone. Never mention salaries, money
  targets, job hunting, or the goal itself in the post.
- Output must fit the schema exactly. No mid-sentence endings, no emojis
  embedded in text, no invented fields.
""".strip()


LINKEDIN_SYSTEM_PROMPT = build_linkedin_system_prompt()


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
        f"Active goal (private context, never mention in post): {context.active_goal}\n"
        if context.active_goal
        else "Active goal: (none)\n"
    )

    opinion_block = ""
    if context.founder_opinion:
        opinion_block = (
            "CARLOS'S OWN PERSPECTIVE — highest priority input.\n"
            "Build the entire post around this. Every paragraph should develop,\n"
            "support, or complicate this point of view. Do NOT summarize the\n"
            "signal instead — the signal is evidence for this argument.\n"
            f'"{context.founder_opinion}"\n\n'
        )

    return (
        f"{opinion_block}"
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
        + (
            "Generate the LinkedIn post. Carlos provided his own perspective above — "
            "start from that, not from the signal summary. The draft fields are "
            "structural scaffolding only."
            if context.founder_opinion
            else "Generate the LinkedIn post fields. Treat the draft hook/points/"
            "closing as a research outline, not a script — rewrite them in the "
            "voice rules above."
        )
    )


def build_linkedin_prompt_kit_text(
    context: LinkedInPostInput,
    language: str | None = None,
) -> tuple[str, str, str]:
    """Build the (system_prompt, user_prompt, one_line_paste_command) tuple.

    Designed to be pasted into Claude / ChatGPT / Cursor. The system_prompt
    is reusable across plans; only user_prompt changes per plan. The one-
    line paste command is a friendly wrapper for chat UIs that prefer a
    single block of text.
    """
    system = build_linkedin_system_prompt(language)
    user = build_linkedin_user_prompt(context)
    one_line = (
        "Eres mi asistente editorial. Lee el contexto y devuélveme un post "
        f"de LinkedIn listo para copiar para el plan #{context.plan_id} "
        "siguiendo las reglas del system prompt al pie de la letra."
    )
    return system, user, one_line
