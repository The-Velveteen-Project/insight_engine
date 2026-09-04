"""
Monthly campaign schemas (Phase 3).

A campaign is one ambitious job lead turned into a four-week plan: the gap
analysis says what is missing, the plan turns that into builds and posts,
Carlos marks items done with evidence, and the month ends with a fresh gap
analysis, a tailored CV and the application.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PlanItemKind(StrEnum):
    BUILD = "build"
    POST = "post"
    LEARN = "learn"
    APPLY = "apply"


class GeneratedPlanItem(BaseModel):
    kind: PlanItemKind
    title: str = Field(min_length=6, max_length=160)
    why: str = Field(min_length=6, max_length=320)
    week: int = Field(ge=1, le=4)


class GeneratedCampaignPlan(BaseModel):
    """What the planner model returns. Numbering and dates are added by code."""

    thesis: str = Field(min_length=20, max_length=480)
    items: list[GeneratedPlanItem] = Field(min_length=3, max_length=8)


class CampaignPlanItem(GeneratedPlanItem):
    n: int = Field(ge=1)
    done_at: datetime | None = None
    evidence_url: str | None = Field(default=None, max_length=600)

    @property
    def done(self) -> bool:
        return self.done_at is not None


class CampaignPlan(BaseModel):
    thesis: str = Field(min_length=20, max_length=480)
    items: list[CampaignPlanItem] = Field(min_length=1, max_length=10)


class CampaignStatus(StrEnum):
    ACTIVE = "active"
    APPLIED = "applied"
    ABANDONED = "abandoned"


class Campaign(BaseModel):
    id: int
    lead_id: int
    goal_id: int | None
    status: CampaignStatus
    started_at: datetime
    target_apply_at: datetime
    plan: CampaignPlan
    lead_title: str
    company: str | None

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.plan.items if item.done)

    @property
    def total_count(self) -> int:
        return len(self.plan.items)

    def days_left(self, now: datetime) -> int:
        return (self.target_apply_at - now).days

    def pending(self) -> list[CampaignPlanItem]:
        return [item for item in self.plan.items if not item.done]

    def done_since(self, since: datetime) -> list[CampaignPlanItem]:
        return [
            item
            for item in self.plan.items
            if item.done_at is not None and item.done_at >= since
        ]
