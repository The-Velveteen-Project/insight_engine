"""
Job radar schemas (Phase 2 of the career manager).

A JobLeadCandidate is what the radar finds and scores. A JobLead is a
persisted row with a pipeline status that Carlos moves by hand
("aplicado 3", "estado 3 entrevista"). Fit is deterministic keyword scoring
against his profile, so every score comes with a readable note.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    NEW = "new"
    SAVED = "saved"
    APPLIED = "applied"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    DISMISSED = "dismissed"


ACTIVE_STATUSES: tuple[JobStatus, ...] = (
    JobStatus.NEW,
    JobStatus.SAVED,
    JobStatus.APPLIED,
    JobStatus.INTERVIEW,
    JobStatus.OFFER,
)


class JobLeadCandidate(BaseModel):
    source: str = Field(min_length=1, max_length=40)
    source_id: str | None = Field(default=None, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    company: str | None = Field(default=None, max_length=160)
    url: str = Field(min_length=8, max_length=1000)
    location: str | None = Field(default=None, max_length=160)
    remote: bool | None = None
    summary: str = Field(default="", max_length=1200)
    published_at: datetime | None = None
    fit_score: float = Field(default=0.0, ge=0.0, le=1.0)
    fit_note: str = Field(default="", max_length=400)
    dream: bool = False


class JobPostingDetails(BaseModel):
    """Structured facts extracted from the posting text. Null means "not stated".

    Salary is normalized to USD per year only when the posting states USD;
    other currencies keep the raw text and leave the numeric fields empty.
    """

    one_line: str = Field(default="", max_length=240)
    company: str | None = Field(default=None, max_length=160)
    salary_text: str | None = Field(default=None, max_length=160)
    salary_min_usd_year: float | None = Field(default=None, ge=0)
    salary_max_usd_year: float | None = Field(default=None, ge=0)
    country: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)
    remote_policy: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    location_restriction: str | None = Field(default=None, max_length=200)
    seniority: str | None = Field(default=None, max_length=60)
    years_required: int | None = Field(default=None, ge=0, le=40)
    must_have: list[str] = Field(default_factory=list, max_length=8)
    nice_to_have: list[str] = Field(default_factory=list, max_length=6)
    education: str | None = Field(default=None, max_length=120)

    def salary_summary(self) -> str | None:
        lo, hi = self.salary_min_usd_year, self.salary_max_usd_year
        if lo is not None and hi is not None and hi > 0:
            return f"USD {lo / 1000:.0f}k–{hi / 1000:.0f}k/año"
        if lo is not None:
            return f"USD {lo / 1000:.0f}k+/año"
        if hi is not None:
            return f"hasta USD {hi / 1000:.0f}k/año"
        return self.salary_text


class JobLead(JobLeadCandidate):
    id: int
    status: JobStatus
    notes: str | None = None
    found_at: datetime
    applied_at: datetime | None = None
    updated_at: datetime | None = None
    details: JobPostingDetails | None = None
    enriched_at: datetime | None = None
    has_posting_text: bool = False
