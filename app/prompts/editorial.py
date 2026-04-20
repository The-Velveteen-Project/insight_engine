"""
Minimal prompts for Phase 6 structured editorial generation.
"""

from __future__ import annotations

from app.schemas.editorial import EditorialGenerationInput
from app.services.context_hub import get_static_context

_SHARED_CONTEXT = get_static_context()

EDITORIAL_SYSTEM_PROMPT = f"""
{_SHARED_CONTEXT}

## Editorial strategist role
You are helping The Velveteen Project prepare a sober editorial or portfolio
proposal from already-curated signals.

Rules:
- Stay technical, clear, and anti-hype.
- Do not invent facts beyond the provided signals.
- Treat weak evidence as weak evidence.
- Do not change the recommended action chosen by the system.
- Keep the language concise and useful.
- The output must fit the supplied schema.
- The outline must contain: hook, points, closing.
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
