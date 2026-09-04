"""
Gap analysis and tailored CV per job lead (Phase 2.6).

The master CV is the only source of facts. It lives outside the public repo:
first in the operator_state table (uploaded through Telegram as a document
with caption "cv_master"), else in a gitignored local file. Lines still
marked [VERIFY] are dropped before any model sees them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.core.config import settings
from app.db.queries import (
    get_job_lead_gap_json,
    get_job_lead_posting_text,
    get_operator_state,
    set_job_lead_cv,
    set_job_lead_gap,
    set_operator_state,
)
from app.prompts.cv import (
    CV_SYSTEM_PROMPT,
    GAP_SYSTEM_PROMPT,
    build_cv_prompt,
    build_gap_prompt,
)
from app.schemas.cv import GapAnalysis, TailoredCV
from app.schemas.jobs import JobLead
from app.services import job_radar
from app.services.generation import get_cv_writer, get_gap_analyst
from app.services.post_ledger import parse_stamp, stamp

logger = logging.getLogger(__name__)

CV_MASTER_KEY = "cv_master"
CV_MASTER_UPDATED_KEY = "cv_master_updated_at"
_VERIFY_MARK = "[VERIFY]"
_POSTING_TEXT_LIMIT = 7000


class MasterCVMissing(LookupError):
    """No master CV in the database nor on disk."""


@dataclass(frozen=True)
class MasterCV:
    text: str
    source: str  # "db" | "file"
    updated_at: datetime | None
    dropped_verify_lines: int

    @property
    def identity_block(self) -> str:
        """Lines under '## Identity' up to the next heading, verbatim."""
        lines = self.text.splitlines()
        collected: list[str] = []
        inside = False
        for line in lines:
            if line.strip().lower().startswith("## identity"):
                inside = True
                continue
            if inside and line.startswith("## "):
                break
            if inside and line.strip():
                collected.append(line.strip())
        return "\n".join(collected) or lines[0].strip() if lines else ""


def _strip_verify(text: str) -> tuple[str, int]:
    kept: list[str] = []
    dropped = 0
    for line in text.splitlines():
        if _VERIFY_MARK in line:
            dropped += 1
            continue
        kept.append(line)
    return "\n".join(kept).strip(), dropped


async def load_master(db: aiosqlite.Connection) -> MasterCV:
    stored = await get_operator_state(db, CV_MASTER_KEY)
    if stored and stored.strip():
        text, dropped = _strip_verify(stored)
        updated = parse_stamp(await get_operator_state(db, CV_MASTER_UPDATED_KEY))
        return MasterCV(
            text=text, source="db", updated_at=updated, dropped_verify_lines=dropped
        )
    path = Path(settings.cv_master_path)
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        text, dropped = _strip_verify(raw)
        if text:
            updated = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            return MasterCV(
                text=text,
                source="file",
                updated_at=updated,
                dropped_verify_lines=dropped,
            )
    raise MasterCVMissing("No master CV available.")


async def save_master(db: aiosqlite.Connection, text: str) -> MasterCV:
    cleaned = text.strip()
    if len(cleaned) < 200:
        raise ValueError("El CV maestro es demasiado corto para ser real.")
    await set_operator_state(db, CV_MASTER_KEY, cleaned)
    await set_operator_state(db, CV_MASTER_UPDATED_KEY, stamp(datetime.now(UTC)))
    return await load_master(db)


def _details_text(lead: JobLead) -> str:
    details = lead.details
    if details is None:
        return ""
    parts: list[str] = []
    if details.one_line:
        parts.append(f"What the job is: {details.one_line}")
    salary = details.salary_summary()
    if salary:
        parts.append(f"Salary: {salary}")
    place = ", ".join(p for p in (details.city, details.country) if p)
    if place:
        parts.append(f"Location: {place} ({details.remote_policy})")
    if details.location_restriction:
        parts.append(f"Restriction: {details.location_restriction}")
    if details.seniority or details.years_required is not None:
        years = f", {details.years_required}+ years" if details.years_required else ""
        parts.append(f"Seniority: {details.seniority or 'not stated'}{years}")
    if details.must_have:
        parts.append("Must have: " + "; ".join(details.must_have))
    if details.nice_to_have:
        parts.append("Nice to have: " + "; ".join(details.nice_to_have))
    return "\n".join(parts)


async def _lead_with_details(db: aiosqlite.Connection, lead_id: int) -> JobLead:
    lead = await job_radar.get_lead(db, lead_id)
    if not lead.has_posting_text and await job_radar.ensure_posting_text(db, lead_id):
        lead = await job_radar.get_lead(db, lead_id)
    if lead.details is None and lead.has_posting_text:
        enriched = await job_radar.enrich_lead(db, lead_id)
        if enriched is not None:
            lead = enriched
    return lead


async def analyze_gap(
    db: aiosqlite.Connection, lead_id: int
) -> tuple[JobLead, GapAnalysis]:
    master = await load_master(db)
    lead = await _lead_with_details(db, lead_id)
    analyst = get_gap_analyst()
    if analyst is None:
        raise RuntimeError("OPENAI_API_KEY vacía: no puedo analizar la brecha.")
    posting_text = (await get_job_lead_posting_text(db, lead_id) or "")[
        :_POSTING_TEXT_LIMIT
    ]
    gap = await analyst.generate(
        system=GAP_SYSTEM_PROMPT,
        user=build_gap_prompt(
            master_cv=master.text,
            title=lead.title,
            company=lead.company,
            details_text=_details_text(lead),
            posting_text=posting_text,
        ),
    )
    if gap is None:
        raise RuntimeError("El modelo no devolvió un análisis de brecha válido.")
    await set_job_lead_gap(db, lead_id=lead_id, gap_json=gap.model_dump_json())
    return lead, gap


async def stored_gap(db: aiosqlite.Connection, lead_id: int) -> GapAnalysis | None:
    raw = await get_job_lead_gap_json(db, lead_id)
    if not raw:
        return None
    try:
        return GapAnalysis.model_validate_json(raw)
    except ValueError:
        return None


def _gap_summary(gap: GapAnalysis) -> str:
    covered = "; ".join(f"{c.requirement} ({c.strength})" for c in gap.covered)
    return (
        f"Verdict: {gap.verdict}. {gap.verdict_reason}\n"
        f"Foreground: {', '.join(gap.foreground)}\n"
        f"Covered: {covered or 'none'}\n"
        f"Missing: {', '.join(gap.missing) or 'none'}\n"
        f"Mirror: {', '.join(gap.keywords_to_mirror) or 'none'}"
    )


async def tailor_cv(
    db: aiosqlite.Connection, lead_id: int
) -> tuple[JobLead, TailoredCV, str, GapAnalysis | None]:
    """Returns (lead, structured cv, markdown, gap used).

    Runs the gap analysis first when none is stored.
    """
    master = await load_master(db)
    lead = await _lead_with_details(db, lead_id)
    gap = await stored_gap(db, lead_id)
    if gap is None:
        try:
            _, gap = await analyze_gap(db, lead_id)
        except RuntimeError as exc:
            logger.warning(
                "Gap analysis skipped before CV for lead %s: %s", lead_id, exc
            )
            gap = None
    writer = get_cv_writer()
    if writer is None:
        raise RuntimeError("OPENAI_API_KEY vacía: no puedo armar el CV.")
    posting_text = (await get_job_lead_posting_text(db, lead_id) or "")[
        :_POSTING_TEXT_LIMIT
    ]
    cv = await writer.generate(
        system=CV_SYSTEM_PROMPT,
        user=build_cv_prompt(
            master_cv=master.text,
            title=lead.title,
            company=lead.company,
            details_text=_details_text(lead),
            posting_text=posting_text,
            gap_summary=_gap_summary(gap) if gap else None,
        ),
    )
    if cv is None:
        raise RuntimeError("El modelo no devolvió un CV válido.")
    markdown = cv.render_markdown(identity_block=master.identity_block)
    await set_job_lead_cv(
        db, lead_id=lead_id, cv_markdown=markdown, generated_at=stamp(datetime.now(UTC))
    )
    return lead, cv, markdown, gap


def cv_filename(lead: JobLead, *, extension: str = "md") -> str:
    company = (lead.company or "role").lower()
    slug = "".join(ch if ch.isalnum() else "-" for ch in company).strip("-") or "role"
    return f"Carlos_Orrego_CV_{slug}_{lead.id}.{extension}"
