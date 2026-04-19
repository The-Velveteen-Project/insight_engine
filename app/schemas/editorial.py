"""
Editorial planning schemas for Phase 6.

The planner decides the action deterministically and always requires
human review. The generator only fills the narrative fields.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator


class RecommendedAction(StrEnum):
    POST = "post"
    NOTE = "note"
    MVP = "mvp"
    ARCHIVE = "archive"


class EditorialPlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SAVED = "saved"
    DISCARDED = "discarded"


class EditorialSignalContext(BaseModel):
    id: int
    source_type: str
    source_id: str | None = None
    title: str
    summary: str | None = None
    url: str | None = None
    relevance_score: float = 0.0
    relevance_note: str = ""
    message_id: int | None = None


class EditorialPlanRequest(BaseModel):
    signal_ids: list[int] = Field(min_length=1, max_length=3)

    @field_validator("signal_ids", mode="before")
    @classmethod
    def _dedupe_ids(cls, value: object) -> list[int]:
        if not isinstance(value, list):
            raise ValueError("signal_ids must be a list of integers.")
        seen: set[int] = set()
        ordered: list[int] = []
        for signal_id in value:
            if not isinstance(signal_id, int):
                raise ValueError("signal_ids must be integers.")
            if signal_id <= 0:
                raise ValueError("signal_ids must be positive integers.")
            if signal_id in seen:
                continue
            seen.add(signal_id)
            ordered.append(signal_id)
        return ordered


class DraftOutline(BaseModel):
    hook: str = Field(min_length=8, max_length=180)
    points: list[str] = Field(min_length=2, max_length=4)
    closing: str = Field(min_length=8, max_length=180)

    @field_validator("points")
    @classmethod
    def _validate_points(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) < 2:
            raise ValueError("points must contain at least 2 non-empty items.")
        return cleaned


class GeneratedEditorialDraft(BaseModel):
    why_it_matters: str = Field(min_length=12, max_length=500)
    angle: str = Field(min_length=8, max_length=240)
    draft_outline: DraftOutline
    portfolio_value: str = Field(min_length=12, max_length=320)


class EditorialGenerationInput(BaseModel):
    recommended_action: RecommendedAction
    confidence: float = Field(ge=0.0, le=1.0)
    rationale_hint: str = Field(min_length=12, max_length=320)
    angle_hint: str = Field(min_length=8, max_length=240)
    signals: list[EditorialSignalContext] = Field(min_length=1, max_length=3)


class DecisionBasis(BaseModel):
    primary_signal_id: int
    supporting_signal_ids: list[int] = Field(default_factory=list, max_length=2)
    source_types: list[str] = Field(min_length=1, max_length=3)
    matched_rule: str = Field(min_length=8, max_length=160)
    confidence_factors: list[str] = Field(min_length=2, max_length=6)

    @field_validator("source_types")
    @classmethod
    def _sort_source_types(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class EditorialPlan(BaseModel):
    signal_ids: list[int] = Field(min_length=1, max_length=3)
    recommended_action: RecommendedAction
    decision_basis: DecisionBasis
    why_it_matters: str = Field(min_length=12, max_length=500)
    angle: str = Field(min_length=8, max_length=240)
    draft_outline: DraftOutline
    portfolio_value: str = Field(min_length=12, max_length=320)
    confidence: float = Field(ge=0.0, le=1.0)
    llm_used: bool
    fallback_used: bool
    needs_human_review: Literal[True] = True

    @model_validator(mode="after")
    def _needs_review(self) -> Self:
        if self.needs_human_review is not True:
            raise ValueError("needs_human_review must remain true in Phase 6.")
        if self.llm_used and self.fallback_used:
            raise ValueError("llm_used and fallback_used cannot both be true.")
        if not self.llm_used and not self.fallback_used:
            raise ValueError("One of llm_used or fallback_used must be true.")
        return self


class PersistedEditorialPlan(BaseModel):
    plan_id: int
    status: EditorialPlanStatus
    proposal: EditorialPlan
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None = None
