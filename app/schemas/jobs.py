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


class JobLead(JobLeadCandidate):
    id: int
    status: JobStatus
    notes: str | None = None
    found_at: datetime
    applied_at: datetime | None = None
    updated_at: datetime | None = None
