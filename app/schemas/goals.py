"""
Schemas for the active goal model and handoff follow-up system (Sub-phase B).

A single active goal at a time. When a new goal is set, the previous one is
archived. The active goal is consumed by the editorial prompt and the weekly
thesis prompt so that filtering and synthesis are anchored to what Carlos is
actually trying to land in the next 12 weeks.

Handoff follow-ups close the loop after a "después" reply to the proactive
MVP handoff suggestion: two days later the operator checks if there is an
existing repo that matches the plan's angle and reaches out accordingly.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ActiveGoal(BaseModel):
    id: int
    label: str = Field(min_length=4, max_length=240)
    description: str | None = Field(default=None, max_length=600)
    deadline_at: datetime | None = None
    created_at: datetime
    archived_at: datetime | None = None


class ActiveGoalDraft(BaseModel):
    label: str = Field(min_length=4, max_length=240)
    description: str | None = Field(default=None, max_length=600)
    deadline_at: datetime | None = None


class HandoffFollowupStatus(StrEnum):
    PENDING = "pending"
    NOTIFIED = "notified"
    DISMISSED = "dismissed"


class HandoffFollowup(BaseModel):
    id: int
    plan_id: int
    chat_id: int
    due_at: datetime
    status: HandoffFollowupStatus
    created_at: datetime
    notified_at: datetime | None = None


class HandoffRepoCandidate(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=400)
    last_activity_summary: str | None = Field(default=None, max_length=400)


class HandoffMatchInput(BaseModel):
    plan_angle: str = Field(min_length=8, max_length=320)
    plan_why: str = Field(min_length=8, max_length=600)
    signal_titles: list[str] = Field(min_length=1, max_length=5)
    repos: list[HandoffRepoCandidate] = Field(min_length=1, max_length=12)


class HandoffRepoMatch(BaseModel):
    """Structured judgment used by the follow-up scheduler.

    `match` is the only field downstream code branches on. The other fields
    enrich the Telegram message when the answer is yes, and are kept short
    so the operator can drop them into the chat without further trimming.
    """

    match: bool
    repo_full_name: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=400)
