"""
Monthly campaign (Phase 3): one ambitious lead, four weeks, evidence.

    objetivo <id>      -> gap analysis (if missing) + planner -> persisted plan
    objetivo           -> progress of the active campaign
    hecho <n> [url]    -> mark plan item n done with its evidence
    abandonar objetivo -> close the campaign without applying

The plan comes from the model once; everything after that is bookkeeping
the Friday recap reads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

from app.db.queries import (
    get_active_campaign,
    get_campaign_by_id,
    insert_campaign,
    update_campaign_plan,
    update_campaign_status,
)
from app.prompts.campaign import CAMPAIGN_SYSTEM_PROMPT, build_campaign_prompt
from app.prompts.project import PROJECT_SYSTEM_PROMPT, build_project_prompt
from app.schemas.campaign import (
    Campaign,
    CampaignPlan,
    CampaignPlanItem,
    CampaignStatus,
    PlanItemKind,
)
from app.schemas.cv import GapAnalysis
from app.schemas.project import ProjectBrief
from app.services import active_goals, cv_tailor, job_radar
from app.services.context_hub import get_static_context
from app.services.generation import get_campaign_planner, get_project_brief_writer
from app.services.post_ledger import parse_stamp, stamp

logger = logging.getLogger(__name__)

CAMPAIGN_DAYS = 28
_MASTER_SUMMARY_CHARS = 3500


class NoActiveCampaign(LookupError):
    """There is no campaign in progress."""


def _row_to_campaign(row: aiosqlite.Row) -> Campaign:
    plan = CampaignPlan.model_validate_json(str(row["plan_json"]))
    return Campaign(
        id=int(row["id"]),
        lead_id=int(row["lead_id"]),
        goal_id=int(row["goal_id"]) if row["goal_id"] is not None else None,
        status=CampaignStatus(str(row["status"])),
        started_at=parse_stamp(row["started_at"]) or datetime.now(UTC),
        target_apply_at=parse_stamp(row["target_apply_at"]) or datetime.now(UTC),
        plan=plan,
        lead_title=str(row["lead_title"]),
        company=str(row["lead_company"]) if row["lead_company"] is not None else None,
    )


async def current(db: aiosqlite.Connection) -> Campaign | None:
    row = await get_active_campaign(db)
    return _row_to_campaign(row) if row is not None else None


def _gap_text(gap: GapAnalysis) -> str:
    covered = "\n".join(
        f"- [{c.strength}] {c.requirement}: {c.evidence}" for c in gap.covered
    )
    missing = "\n".join(f"- {item}" for item in gap.missing) or "- (none stated)"
    return (
        f"Verdict: {gap.verdict}. {gap.verdict_reason}\n"
        f"Covered:\n{covered or '- (none)'}\n"
        f"Missing:\n{missing}\n"
        f"Foreground: {', '.join(gap.foreground)}\n"
        f"Keywords to mirror: {', '.join(gap.keywords_to_mirror) or '(none)'}"
    )


def _ensure_apply_item(items: list[CampaignPlanItem]) -> list[CampaignPlanItem]:
    if any(item.kind is PlanItemKind.APPLY for item in items):
        return items
    items.append(
        CampaignPlanItem(
            n=len(items) + 1,
            kind=PlanItemKind.APPLY,
            title="Brecha de cierre, CV a la medida y aplicación enviada",
            why="Cierra el mes con la evidencia nueva ya visible.",
            week=4,
        )
    )
    return items


async def start(
    db: aiosqlite.Connection, lead_id: int, *, now: datetime | None = None
) -> tuple[Campaign, GapAnalysis]:
    """Create the campaign for `lead_id`, abandoning any active one."""
    moment = now or datetime.now(UTC)
    gap = await cv_tailor.stored_gap(db, lead_id)
    if gap is None:
        _, gap = await cv_tailor.analyze_gap(db, lead_id)
    lead = await job_radar.get_lead(db, lead_id)
    master = await cv_tailor.load_master(db)
    planner = get_campaign_planner()
    if planner is None:
        raise RuntimeError("OPENAI_API_KEY vacía: no puedo armar el plan.")
    generated = await planner.generate(
        system=CAMPAIGN_SYSTEM_PROMPT,
        user=build_campaign_prompt(
            lead_title=lead.title,
            company=lead.company,
            gap_text=_gap_text(gap),
            master_summary=master.text[:_MASTER_SUMMARY_CHARS],
        ),
    )
    if generated is None:
        raise RuntimeError("El modelo no devolvió un plan válido.")

    ordered = sorted(generated.items, key=lambda item: (item.week, item.kind.value))
    items = [
        CampaignPlanItem(n=index, **item.model_dump())
        for index, item in enumerate(ordered, start=1)
    ]
    items = _ensure_apply_item(items)
    plan = CampaignPlan(thesis=generated.thesis, items=items)

    previous = await get_active_campaign(db)
    if previous is not None:
        await update_campaign_status(
            db, campaign_id=int(previous["id"]), status=CampaignStatus.ABANDONED.value
        )
    goal = await active_goals.get_current(db)
    campaign_id = await insert_campaign(
        db,
        lead_id=lead_id,
        goal_id=goal.id if goal is not None else None,
        started_at=stamp(moment),
        target_apply_at=stamp(moment + timedelta(days=CAMPAIGN_DAYS)),
        plan_json=plan.model_dump_json(),
    )
    row = await get_campaign_by_id(db, campaign_id)
    assert row is not None
    return _row_to_campaign(row), gap


async def mark_done(
    db: aiosqlite.Connection,
    item_n: int,
    *,
    evidence_url: str | None,
    now: datetime | None = None,
) -> tuple[Campaign, CampaignPlanItem]:
    campaign = await current(db)
    if campaign is None:
        raise NoActiveCampaign("No hay objetivo del mes activo.")
    target = next((item for item in campaign.plan.items if item.n == item_n), None)
    if target is None:
        raise LookupError(f"El plan no tiene un ítem {item_n}.")
    moment = now or datetime.now(UTC)
    updated_items = [
        item.model_copy(
            update={
                "done_at": moment,
                "evidence_url": evidence_url or item.evidence_url,
            }
        )
        if item.n == item_n
        else item
        for item in campaign.plan.items
    ]
    plan = CampaignPlan(thesis=campaign.plan.thesis, items=updated_items)
    await update_campaign_plan(
        db, campaign_id=campaign.id, plan_json=plan.model_dump_json()
    )
    if target.kind is PlanItemKind.APPLY:
        await update_campaign_status(
            db, campaign_id=campaign.id, status=CampaignStatus.APPLIED.value
        )
    refreshed_row = await get_campaign_by_id(db, campaign.id)
    assert refreshed_row is not None
    refreshed = _row_to_campaign(refreshed_row)
    done_item = next(item for item in refreshed.plan.items if item.n == item_n)
    return refreshed, done_item


async def abandon(db: aiosqlite.Connection) -> Campaign:
    campaign = await current(db)
    if campaign is None:
        raise NoActiveCampaign("No hay objetivo del mes activo.")
    await update_campaign_status(
        db, campaign_id=campaign.id, status=CampaignStatus.ABANDONED.value
    )
    row = await get_campaign_by_id(db, campaign.id)
    assert row is not None
    return _row_to_campaign(row)


async def project_brief(
    db: aiosqlite.Connection, item_n: int
) -> tuple[Campaign, CampaignPlanItem, ProjectBrief, str]:
    """Brief for one build item of the active campaign: (campaign, item, brief, md)."""
    campaign = await current(db)
    if campaign is None:
        raise NoActiveCampaign("No hay objetivo del mes activo.")
    item = next((it for it in campaign.plan.items if it.n == item_n), None)
    if item is None:
        raise LookupError(f"El plan no tiene un ítem {item_n}.")
    if item.kind not in (PlanItemKind.BUILD, PlanItemKind.LEARN):
        raise ValueError(
            f"La pieza {item_n} es un {item.kind.value}; el brief es para builds."
        )
    gap = await cv_tailor.stored_gap(db, campaign.lead_id)
    master = await cv_tailor.load_master(db)
    writer = get_project_brief_writer()
    if writer is None:
        raise RuntimeError("OPENAI_API_KEY vacía: no puedo armar el brief.")
    brief = await writer.generate(
        system=PROJECT_SYSTEM_PROMPT,
        user=build_project_prompt(
            item_title=item.title,
            item_why=item.why,
            campaign_thesis=campaign.plan.thesis,
            lead_title=campaign.lead_title,
            company=campaign.company,
            gap_text=_gap_text(gap) if gap is not None else "(no gap analysis stored)",
            master_summary=master.text[:_MASTER_SUMMARY_CHARS],
            velveteen_context=get_static_context()[:3000],
        ),
    )
    if brief is None:
        raise RuntimeError("El modelo no devolvió un brief válido.")
    return campaign, item, brief, brief.render_markdown()


def brief_filename(campaign: Campaign, item: CampaignPlanItem) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in item.title.lower())[:40]
    return f"brief_objetivo{campaign.id}_pieza{item.n}_{slug.strip('-')}.md"


@dataclass(frozen=True)
class CampaignProgress:
    campaign: Campaign
    done: int
    total: int
    days_left: int
    next_items: list[CampaignPlanItem]
    done_this_week: list[CampaignPlanItem]

    @property
    def pending_left(self) -> int:
        return self.total - self.done

    @property
    def week_number(self) -> int:
        elapsed = (self.campaign.target_apply_at - self.campaign.started_at).days
        used = elapsed - self.days_left
        return max(1, min(4, used // 7 + 1))


def progress(campaign: Campaign, *, now: datetime | None = None) -> CampaignProgress:
    moment = now or datetime.now(UTC)
    pending = campaign.pending()
    return CampaignProgress(
        campaign=campaign,
        done=campaign.done_count,
        total=campaign.total_count,
        days_left=campaign.days_left(moment),
        next_items=sorted(pending, key=lambda item: (item.week, item.n))[:3],
        done_this_week=campaign.done_since(moment - timedelta(days=7)),
    )
