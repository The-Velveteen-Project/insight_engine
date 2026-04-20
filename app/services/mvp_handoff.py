"""
Conservative MVP handoff generation.

Builds a structured prompt pack from an already persisted editorial plan when
that plan has been classified as an MVP candidate.
"""

from __future__ import annotations

import aiosqlite

from app.db.queries import get_signals_by_ids
from app.schemas.editorial import RecommendedAction
from app.schemas.mvp_handoff import MvpHandoffPack, MvpPromptBundle
from app.services.context_hub import build_dynamic_context, get_static_context
from app.services.editorial_planner import get_persisted_editorial_plan


class MvpHandoffStateError(Exception):
    """Raised when the requested plan is not eligible for MVP handoff."""


def _compact(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


async def create_mvp_handoff_pack(
    db: aiosqlite.Connection,
    plan_id: int,
) -> MvpHandoffPack:
    plan = await get_persisted_editorial_plan(db, plan_id)
    proposal = plan.proposal
    if proposal.recommended_action != RecommendedAction.MVP:
        raise MvpHandoffStateError(
            "MVP handoff is only available for plans with recommended_action=mvp."
        )

    signal_rows = await get_signals_by_ids(db, proposal.signal_ids)
    signal_titles = [str(row["title"] or "") for row in signal_rows]
    static_context = get_static_context()
    dynamic_context = await build_dynamic_context(db)

    thesis = proposal.angle
    scope_summary = _compact(
        (
            f"{proposal.why_it_matters} "
            f"Hook: {proposal.draft_outline.hook}. "
            f"Points: {'; '.join(proposal.draft_outline.points)}. "
            f"Closing: {proposal.draft_outline.closing}. "
            f"Portfolio value: {proposal.portfolio_value}"
        ),
        500,
    )

    prompt_architect = MvpPromptBundle(
        system_prompt=(
            f"{static_context}\n\n"
            "You are the MVP Prompt Architect for The Velveteen Project. "
            "Your job is to turn a validated MVP plan into an implementation "
            "prompt for a coding model. Preserve rigor, scope control, and "
            "Velveteen tone. Do not inflate the build."
        ),
        user_prompt=(
            f"Plan id: {plan_id}\n"
            f"Signal ids: {proposal.signal_ids}\n"
            f"Thesis: {thesis}\n"
            f"Scope summary: {scope_summary}\n"
            f"Signal titles: {signal_titles}\n"
            "Write a production-grade prompt for a builder model such as Codex "
            "or Antigravity. The prompt must define problem, scope, files or "
            "modules to create, APIs or data sources, constraints, tests, and "
            "acceptance checks."
        ),
    )

    builder = MvpPromptBundle(
        system_prompt=(
            f"{static_context}\n\n"
            "You are a coding model building a small, real MVP for Carlos "
            "Manuel Orrego and The Velveteen Project. Work like a serious "
            "backend/AI engineer. Keep the build narrow, testable, and aligned "
            "with the analytical core."
        ),
        user_prompt=(
            f"Build an MVP from plan #{plan_id}.\n"
            f"Thesis: {thesis}\n"
            f"Scope summary: {scope_summary}\n"
            f"Signal ids: {proposal.signal_ids}\n"
            f"Signal titles: {signal_titles}\n"
            "Requirements:\n"
            "- keep deterministic logic explicit\n"
            "- avoid fake multi-agent theatrics\n"
            "- expose clear interfaces\n"
            "- include tests\n"
            "- document tradeoffs briefly\n"
            "Output: code changes, tests, and a short implementation summary."
        ),
    )

    auditor = MvpPromptBundle(
        system_prompt=(
            f"{static_context}\n\n"
            "You are the MVP Auditor for The Velveteen Project. Review the "
            "produced MVP like a sober Staff Engineer. Prioritize correctness, "
            "scope control, rigor, and whether the build actually fits the plan."
        ),
        user_prompt=(
            f"Audit the MVP produced from plan #{plan_id}.\n"
            f"Thesis: {thesis}\n"
            f"Scope summary: {scope_summary}\n"
            f"Signal titles: {signal_titles}\n\n"
            f"{dynamic_context}\n\n"
            "Check:\n"
            "- whether the implementation matches the stated scope\n"
            "- whether deterministic logic stayed outside the LLM path\n"
            "- whether tests and failure modes were covered\n"
            "- whether the result still feels like Velveteen rather than "
            "a generic AI demo\n"
            "Return findings first, then residual risks."
        ),
    )

    return MvpHandoffPack(
        plan_id=plan_id,
        signal_ids=proposal.signal_ids,
        thesis=thesis,
        scope_summary=scope_summary,
        context_basis=[
            "shared_velveteen_context",
            "dynamic_db_snapshot",
            "persisted_editorial_plan",
            "persisted_signals",
        ],
        prompt_architect=prompt_architect,
        builder=builder,
        auditor=auditor,
    )
