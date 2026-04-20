"""
Structured editorial draft schemas for Phase 8.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.editorial import RecommendedAction


class EditorialDraftStatus(StrEnum):
    DRAFT = "draft"
    DISCARDED = "discarded"


class EditorialDraftContent(BaseModel):
    working_title: str = Field(min_length=12, max_length=160)
    post_body: str = Field(min_length=80, max_length=2200)
    short_version: str = Field(min_length=40, max_length=420)
    cta: str = Field(min_length=8, max_length=180)
    tone_notes: list[str] = Field(min_length=2, max_length=4)

    @field_validator("tone_notes")
    @classmethod
    def _validate_tone_notes(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) < 2:
            raise ValueError("tone_notes must contain at least 2 non-empty items.")
        return cleaned


class DraftGenerationInput(BaseModel):
    plan_id: int
    signal_ids: list[int] = Field(min_length=1, max_length=3)
    recommended_action: RecommendedAction
    source_angle: str = Field(min_length=8, max_length=240)
    why_it_matters: str = Field(min_length=12, max_length=500)
    draft_hook: str = Field(min_length=8, max_length=180)
    draft_points: list[str] = Field(min_length=2, max_length=4)
    draft_closing: str = Field(min_length=8, max_length=180)
    portfolio_value: str = Field(min_length=12, max_length=320)


class EditorialDraft(BaseModel):
    plan_id: int
    signal_ids: list[int] = Field(min_length=1, max_length=3)
    recommended_action: RecommendedAction
    source_angle: str = Field(min_length=8, max_length=240)
    llm_used: bool
    fallback_used: bool
    content: EditorialDraftContent

    @model_validator(mode="after")
    def _validate_generation_flags(self) -> Self:
        if self.llm_used and self.fallback_used:
            raise ValueError("llm_used and fallback_used cannot both be true.")
        if not self.llm_used and not self.fallback_used:
            raise ValueError("One of llm_used or fallback_used must be true.")
        return self


class PersistedEditorialDraft(BaseModel):
    draft_id: int
    plan_id: int
    status: EditorialDraftStatus
    draft: EditorialDraft
    created_at: datetime
    updated_at: datetime
