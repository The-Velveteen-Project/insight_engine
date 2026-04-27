"""
Handoff follow-up service for Sub-phase B.

When Carlos replies "después" to a proactive MVP handoff offer, we schedule
a follow-up two days out. A daily cron processes due rows: for each one we
ask whether any of his priority repos already addresses the plan's angle
and reach out with the right framing — escalation question if there is a
match, gentle reminder if there isn't.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import aiosqlite

from app.core.config import settings
from app.db.queries import (
    get_due_handoff_followups,
    get_pending_handoff_followups_for_chat,
    get_signals_by_ids,
    get_telegram_session,
    insert_handoff_followup,
    mark_handoff_followup_dismissed,
    mark_handoff_followup_notified,
    upsert_telegram_session,
)
from app.integrations.github_client import (
    fetch_recent_commits,
    fetch_repo_metadata,
)
from app.integrations.telegram_client import send_message
from app.schemas.commands import CommandName
from app.schemas.editorial import PersistedEditorialPlan
from app.schemas.goals import (
    HandoffMatchInput,
    HandoffRepoCandidate,
    HandoffRepoMatch,
)
from app.services.editorial_planner import get_persisted_editorial_plan
from app.services.generation import get_handoff_matcher
from app.utils import telegram_formatting

logger = logging.getLogger(__name__)


async def schedule_after_postpone(
    db: aiosqlite.Connection,
    *,
    plan_id: int,
    chat_id: int,
) -> int:
    delay = timedelta(hours=settings.handoff_followup_delay_hours)
    due_at = (datetime.now(tz=UTC) + delay).isoformat()
    return await insert_handoff_followup(
        db,
        plan_id=plan_id,
        chat_id=chat_id,
        due_at=due_at,
    )


async def dismiss_latest_for_chat(
    db: aiosqlite.Connection,
    chat_id: int,
) -> bool:
    rows = await get_pending_handoff_followups_for_chat(db, chat_id)
    if not rows:
        return False
    await mark_handoff_followup_dismissed(db, int(rows[0]["id"]))
    return True


def _summarize_recent_activity(commits: list[object]) -> str | None:
    if not commits:
        return None
    # commits are GitHubCommitSummary (Pydantic). Take the most recent few
    # subjects (first line of each message) for matcher context.
    subjects: list[str] = []
    for commit in commits[:3]:
        message = getattr(commit, "message", "") or ""
        subject = message.splitlines()[0].strip() if message else ""
        if subject:
            subjects.append(subject)
    if not subjects:
        return None
    return " · ".join(subjects)


async def _build_repo_candidates(
    repo_names: tuple[str, ...],
) -> list[HandoffRepoCandidate]:
    candidates: list[HandoffRepoCandidate] = []
    for full_name in repo_names:
        try:
            metadata = await fetch_repo_metadata(full_name)
            commits = await fetch_recent_commits(full_name, per_page=5)
        except Exception as exc:
            logger.warning(
                "Skipping repo %s in handoff match: %s", full_name, exc
            )
            continue
        candidates.append(
            HandoffRepoCandidate(
                full_name=metadata.full_name,
                description=metadata.description,
                last_activity_summary=_summarize_recent_activity(list(commits)),
            )
        )
    return candidates


def _deterministic_match(
    plan: PersistedEditorialPlan,
    repos: list[HandoffRepoCandidate],
) -> HandoffRepoMatch:
    """Keyword-overlap fallback used when the LLM matcher is unavailable."""
    angle_tokens = {
        token.lower().strip(".,;:")
        for token in plan.proposal.angle.split()
        if len(token) >= 4
    }
    best: tuple[float, HandoffRepoCandidate | None] = (0.0, None)
    for repo in repos:
        haystack = " ".join(
            filter(
                None,
                [
                    repo.full_name,
                    repo.description or "",
                    repo.last_activity_summary or "",
                ],
            )
        ).lower()
        hits = sum(1 for token in angle_tokens if token in haystack)
        score = hits / max(len(angle_tokens), 1)
        if score > best[0]:
            best = (score, repo)
    score, winner = best
    if winner is None or score < 0.25:
        return HandoffRepoMatch(match=False, confidence=score)
    return HandoffRepoMatch(
        match=True,
        repo_full_name=winner.full_name,
        confidence=min(score, 0.6),
        rationale=(
            f"Tu repo `{winner.full_name}` comparte vocabulario con el ángulo "
            "del plan; vale la pena verlo en concreto."
        ),
    )


async def match_plan_to_repos(
    plan: PersistedEditorialPlan,
    repos: list[HandoffRepoCandidate],
    *,
    signal_titles: list[str],
) -> HandoffRepoMatch:
    if not repos:
        return HandoffRepoMatch(match=False)
    matcher = get_handoff_matcher()
    if matcher is not None:
        try:
            llm_judgment = await matcher.generate(
                HandoffMatchInput(
                    plan_angle=plan.proposal.angle,
                    plan_why=plan.proposal.why_it_matters,
                    signal_titles=signal_titles,
                    repos=repos,
                )
            )
        except Exception as exc:  # belt-and-suspenders
            logger.warning("Handoff matcher raised: %s", exc)
            llm_judgment = None
        if llm_judgment is not None:
            return llm_judgment
    return _deterministic_match(plan, repos)


async def _set_chat_pending_handoff(
    db: aiosqlite.Connection,
    chat_id: int,
    plan_id: int,
) -> None:
    """Persist pending=MVP_HANDOFF so the user's `hazlo` triggers the handoff."""
    row = await get_telegram_session(db, chat_id)
    if row is None:
        last_signal_ids: list[int] = []
        last_draft_id: int | None = None
    else:
        try:
            last_signal_ids = list(json.loads(str(row["last_signal_ids"] or "[]")))
        except (TypeError, ValueError):
            last_signal_ids = []
        last_draft_id = (
            int(row["last_draft_id"]) if row["last_draft_id"] is not None else None
        )
    await upsert_telegram_session(
        db,
        chat_id=chat_id,
        last_signal_ids=last_signal_ids,
        last_plan_id=plan_id,
        last_draft_id=last_draft_id,
        pending_command=CommandName.MVP_HANDOFF.value,
        pending_target_id=plan_id,
    )
    # Invalidate orchestrator's in-memory cache so the next webhook reads
    # the freshly written pending state instead of a stale dict entry.
    from app.services import telegram_orchestrator

    telegram_orchestrator.invalidate_cached_state(chat_id)


async def _signal_titles_for_plan(
    db: aiosqlite.Connection,
    plan: PersistedEditorialPlan,
) -> list[str]:
    rows = await get_signals_by_ids(db, plan.proposal.signal_ids)
    titles = [str(row["title"] or "(sin título)") for row in rows]
    return titles or ["(plan sin señales asociadas)"]


async def process_due_followups(db: aiosqlite.Connection) -> int:
    rows = await get_due_handoff_followups(db)
    if not rows:
        return 0

    repos = await _build_repo_candidates(settings.priority_github_repo_list)
    sent = 0
    for row in rows:
        followup_id = int(row["id"])
        plan_id = int(row["plan_id"])
        chat_id = int(row["chat_id"])
        try:
            plan = await get_persisted_editorial_plan(db, plan_id)
        except LookupError:
            logger.info(
                "Followup %s skipped: plan %s no longer exists.",
                followup_id,
                plan_id,
            )
            await mark_handoff_followup_dismissed(db, followup_id)
            continue

        signal_titles = await _signal_titles_for_plan(db, plan)
        match = await match_plan_to_repos(
            plan,
            repos,
            signal_titles=signal_titles,
        )

        if match.match and match.repo_full_name:
            text = telegram_formatting.format_handoff_followup_with_match(
                plan_id=plan_id,
                plan_angle=plan.proposal.angle,
                repo_full_name=match.repo_full_name,
                rationale=match.rationale or "",
            )
        else:
            text = telegram_formatting.format_handoff_followup_no_match(
                plan_id=plan_id,
                plan_angle=plan.proposal.angle,
            )

        await _set_chat_pending_handoff(db, chat_id, plan_id)
        try:
            await send_message(chat_id, text)
        except Exception as exc:
            logger.error("Failed to send handoff followup to chat %s: %s", chat_id, exc)
            continue
        await mark_handoff_followup_notified(db, followup_id)
        sent += 1
    return sent
