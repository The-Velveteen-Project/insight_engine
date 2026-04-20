"""
Minimal prompts for Phase 8 structured draft generation.
"""

from __future__ import annotations

from app.schemas.drafts import DraftGenerationInput
from app.services.context_hub import get_static_context

_SHARED_CONTEXT = get_static_context()

DRAFT_SYSTEM_PROMPT = f"""
{_SHARED_CONTEXT}

## Draft writer role
You are helping The Velveteen Project turn an approved editorial plan into a
usable draft.

Rules:
- Stay sober, technical, and anti-hype.
- Do not invent facts beyond the approved plan.
- Keep the draft concise and reusable.
- Avoid marketing language and broad claims.
- The output must fit the supplied schema exactly.
""".strip()


def build_draft_prompt(context: DraftGenerationInput) -> str:
    points = "\n".join(f"- {point}" for point in context.draft_points)
    return (
        f"Plan id: {context.plan_id}\n"
        f"Signal ids: {context.signal_ids}\n"
        f"Recommended action: {context.recommended_action.value}\n"
        f"Source angle: {context.source_angle}\n"
        f"Why it matters: {context.why_it_matters}\n"
        f"Hook: {context.draft_hook}\n"
        f"Points:\n{points}\n"
        f"Closing: {context.draft_closing}\n"
        f"Portfolio value: {context.portfolio_value}\n\n"
        "Write a structured draft from this approved plan."
    )
