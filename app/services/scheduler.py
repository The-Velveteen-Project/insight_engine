"""
Minimal local scheduler for Phase 9.

Implements a tiny subset of cron suitable for weekly jobs:
- minute hour * * *
- minute hour * * dow
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite

from app.core.config import settings
from app.integrations.telegram_client import send_message
from app.services import telegram_orchestrator
from app.utils import telegram_formatting

logger = logging.getLogger(__name__)

JobRunner = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class CronSpec:
    minute: int
    hour: int
    day_of_week: int | None = None


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    cron: CronSpec
    runner: JobRunner


def parse_cron(expr: str) -> CronSpec:
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"Unsupported cron expression: {expr!r}")

    minute_str, hour_str, day_of_month, month, day_of_week = fields
    if day_of_month != "*" or month != "*":
        raise ValueError(f"Unsupported cron expression: {expr!r}")

    minute = int(minute_str)
    hour = int(hour_str)
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        raise ValueError(f"Unsupported cron expression: {expr!r}")

    if day_of_week == "*":
        return CronSpec(minute=minute, hour=hour, day_of_week=None)

    dow = int(day_of_week)
    if not 0 <= dow <= 6:
        raise ValueError(f"Unsupported cron expression: {expr!r}")
    return CronSpec(minute=minute, hour=hour, day_of_week=dow)


def next_run_after(now: datetime, cron: CronSpec) -> datetime:
    candidate = now.replace(
        hour=cron.hour,
        minute=cron.minute,
        second=0,
        microsecond=0,
    )
    if cron.day_of_week is None:
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    python_dow = (cron.day_of_week - 1) % 7
    days_ahead = (python_dow - candidate.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


async def run_weekly_summary_job() -> None:
    if settings.telegram_admin_chat_id <= 0:
        logger.info(
            "Weekly summary job skipped: TELEGRAM_ADMIN_CHAT_ID not configured."
        )
        return

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        summary = await telegram_orchestrator.build_weekly_summary(
            db,
            query=settings.weekly_discovery_query,
        )

    if summary is None:
        text = "<b>Weekly summary</b>\nNo useful signals found this run."
    else:
        text = telegram_formatting.format_weekly_summary(summary)
    await send_message(settings.telegram_admin_chat_id, text)


async def run_weekly_mvp_scan_job() -> None:
    if settings.telegram_admin_chat_id <= 0:
        logger.info("Weekly MVP scan skipped: TELEGRAM_ADMIN_CHAT_ID not configured.")
        return

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        idea = await telegram_orchestrator.build_mvp_idea(
            db,
            settings.weekly_discovery_query,
        )

    text = telegram_formatting.format_mvp_idea(idea)
    await send_message(settings.telegram_admin_chat_id, text)


class LocalScheduler:
    def __init__(self, jobs: list[ScheduledJob]) -> None:
        self._jobs = jobs
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for job in self._jobs:
            self._tasks.append(asyncio.create_task(self._run_job_loop(job)))
        logger.info("Local scheduler started with %d jobs.", len(self._jobs))

    async def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Local scheduler stopped.")

    async def _run_job_loop(self, job: ScheduledJob) -> None:
        while True:
            now = datetime.now().astimezone()
            scheduled_at = next_run_after(now, job.cron)
            delay = max((scheduled_at - now).total_seconds(), 1.0)
            await asyncio.sleep(delay)
            try:
                await job.runner()
                logger.info("Scheduler job %r completed.", job.name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Scheduler job %r failed: %s", job.name, exc)


def build_scheduler() -> LocalScheduler | None:
    if not settings.enable_scheduler:
        return None

    jobs = [
        ScheduledJob(
            name="weekly_summary",
            cron=parse_cron(settings.weekly_summary_cron),
            runner=run_weekly_summary_job,
        ),
        ScheduledJob(
            name="weekly_mvp_scan",
            cron=parse_cron(settings.weekly_mvp_scan_cron),
            runner=run_weekly_mvp_scan_job,
        ),
    ]
    return LocalScheduler(jobs)
