"""
Friday recap (Phase 3): what the week actually moved.

Everything here is read from the database and GitHub; no model call. The
verdict is a small deterministic rule so it stays honest and cheap:

    posts published      >= 2 -> 1 point (1 post -> 0.5)
    realistic applications >= 2 -> 1 point (1 -> 0.5)
    campaign item done   >= 1 -> 1 point; without a campaign, >= 3 commits
                                 in the priority repos count instead
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import aiosqlite

from app.core.config import settings
from app.db.queries import (
    count_job_leads_found_since,
    list_job_leads_applied_since,
    list_job_leads_moved_since,
    list_linkedin_posts_published_since,
)
from app.integrations import github_client
from app.schemas.jobs import JobLead
from app.schemas.linkedin import LinkedInPostRecord
from app.services import campaign as campaign_service
from app.services import job_radar, post_ledger
from app.services.post_ledger import stamp

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 7


@dataclass(frozen=True)
class RecapReport:
    since: datetime
    now: datetime
    posts: list[LinkedInPostRecord]
    cadence_target: int
    applied: list[JobLead]
    moved: list[JobLead]
    new_realistic: int
    new_dream: int
    campaign: campaign_service.CampaignProgress | None
    commits_by_repo: dict[str, int] | None
    score: float
    verdict: str
    reasons: list[str] = field(default_factory=list)


def _score(
    *,
    posts: int,
    applied: int,
    campaign_done: int | None,
    commits: int | None,
) -> tuple[float, str, list[str]]:
    reasons: list[str] = []
    total = 0.0
    if posts >= 2:
        total += 1.0
        reasons.append(f"{posts} posts publicados")
    elif posts == 1:
        total += 0.5
        reasons.append("1 post publicado, la meta son 2")
    else:
        reasons.append("ningún post publicado")

    if applied >= 2:
        total += 1.0
        reasons.append(f"{applied} aplicaciones realistas")
    elif applied == 1:
        total += 0.5
        reasons.append("1 aplicación, la meta son 2")
    else:
        reasons.append("ninguna aplicación")

    if campaign_done is not None:
        if campaign_done >= 1:
            total += 1.0
            reasons.append(f"{campaign_done} pieza(s) del objetivo del mes cerradas")
        else:
            reasons.append("el objetivo del mes no avanzó")
    elif commits is not None:
        if commits >= 3:
            total += 1.0
            reasons.append(f"{commits} commits en tus repos prioritarios")
        else:
            reasons.append(f"solo {commits} commits en tus repos prioritarios")
    else:
        reasons.append("sin objetivo del mes ni lectura de repos")

    if total >= 2.5:
        verdict = "Semana que suma. Esto es lo que acerca la meta."
    elif total >= 1.5:
        verdict = "Semana a medias. Hubo movimiento, pero no en todas las palancas."
    else:
        verdict = "Semana sin movimiento real. No pasa nada, pero hay que decirlo."
    return total, verdict, reasons


async def _commits_last_week(since: datetime) -> dict[str, int] | None:
    repos = settings.priority_github_repo_list
    if not repos:
        return None

    async def one(repo: str) -> tuple[str, int | None]:
        try:
            commits = await github_client.fetch_recent_commits(repo, per_page=30)
        except Exception as exc:
            logger.warning("Commit count for %s failed: %s", repo, exc)
            return repo, None
        count = sum(
            1 for c in commits if c.committed_at is not None and c.committed_at >= since
        )
        return repo, count

    results = await asyncio.gather(*(one(repo) for repo in repos))
    counted = {repo: count for repo, count in results if count is not None}
    return counted or None


async def build_recap(
    db: aiosqlite.Connection, *, now: datetime | None = None
) -> RecapReport:
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=_WINDOW_DAYS)
    since_stamp = stamp(since)

    post_rows = await list_linkedin_posts_published_since(db, since_stamp)
    posts = [post_ledger._row_to_record(row) for row in post_rows]
    applied = [
        job_radar._row_to_lead(row)
        for row in await list_job_leads_applied_since(db, since_stamp)
    ]
    moved = [
        job_radar._row_to_lead(row)
        for row in await list_job_leads_moved_since(db, since_stamp)
    ]
    new_realistic, new_dream = await count_job_leads_found_since(db, since_stamp)

    active = await campaign_service.current(db)
    campaign_progress = (
        campaign_service.progress(active, now=moment) if active is not None else None
    )
    commits = await _commits_last_week(since)

    realistic_applied = [lead for lead in applied if not lead.dream]
    score, verdict, reasons = _score(
        posts=len(posts),
        applied=len(realistic_applied),
        campaign_done=(
            len(campaign_progress.done_this_week) if campaign_progress else None
        ),
        commits=sum(commits.values()) if commits else None,
    )
    return RecapReport(
        since=since,
        now=moment,
        posts=posts,
        cadence_target=max(settings.post_cadence_per_week, 1),
        applied=applied,
        moved=moved,
        new_realistic=new_realistic,
        new_dream=new_dream,
        campaign=campaign_progress,
        commits_by_repo=commits,
        score=score,
        verdict=verdict,
        reasons=reasons,
    )
