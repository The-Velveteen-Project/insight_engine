"""
Job radar (Phase 2 of the career manager).

Searches job boards through Exa for roles that match the active goal,
scores each lead deterministically against Carlos's profile, persists the
new ones, and keeps a small application pipeline he moves by hand.

Design choices:
- Fit is keyword scoring with a readable note, not an LLM judgment. The
  score decides ordering; Carlos decides applications.
- Dream companies (Anthropic and peers) are flagged and sorted first even
  when the fit score is modest, because the goal names them explicitly.
- The companies the goal names are read from their own boards (Greenhouse,
  Ashby) before Exa runs, so a new Anthropic or OpenAI posting shows up the
  week it appears, not when a search engine indexes it. Board postings pass
  a title gate (research, scientist, fellow, ML) instead of the fit
  threshold: they are dream leads by definition, the gate keeps recruiters
  and managers out.
- Every run reports what it tried: how many results came back, how many
  were already known, and whether the source failed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import unquote, urlsplit

import aiosqlite

from app.core.config import settings
from app.db.queries import (
    count_job_leads_by_status,
    get_job_lead_by_id,
    insert_job_lead,
    known_job_lead_urls,
    list_job_leads,
    list_job_leads_pending_enrichment,
    set_job_lead_posting_text,
    update_job_lead_details,
    update_job_lead_status,
)
from app.integrations import exa_client, job_boards
from app.schemas.jobs import (
    ACTIVE_STATUSES,
    JobLead,
    JobLeadCandidate,
    JobPostingDetails,
    JobStatus,
)
from app.services.generation import get_job_details_extractor
from app.services.post_ledger import parse_stamp, stamp
from app.utils.text import trim_to_boundary

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fit scoring tables (profile: research engineer / scientific ML / applied math)
# ---------------------------------------------------------------------------

_ROLE_TERMS: dict[str, float] = {
    "research engineer": 0.35,
    "research scientist": 0.35,
    "applied scientist": 0.3,
    "scientific machine learning": 0.35,
    "scientific ml": 0.35,
    "machine learning engineer": 0.25,
    "ml engineer": 0.25,
    "ai engineer": 0.2,
    "ai scientist": 0.3,
    "machine learning scientist": 0.35,
    "ml scientist": 0.35,
    "research fellow": 0.3,
    "fellows program": 0.35,
    "fellowship": 0.3,
    "member of technical staff": 0.3,
    "scientist, machine learning": 0.35,
    "forward deployed engineer": 0.15,
    "ml researcher": 0.3,
    "ai researcher": 0.3,
    "computational biologist": 0.25,
    "data scientist": 0.12,
    "quantitative": 0.15,
}
_DOMAIN_TERMS: tuple[str, ...] = (
    "stochastic",
    "forecasting",
    "time series",
    "bioinformatics",
    "computational biology",
    "genomics",
    "protein",
    "foundation model",
    "large language model",
    "llm",
    "agent",
    "climate",
    "risk",
    "bayesian",
    "applied mathematics",
    "simulation",
    "differential equation",
    "pytorch",
    "jax",
    "scientific computing",
    "healthcare",
)
_LOCATION_TERMS: tuple[str, ...] = (
    "remote",
    "worldwide",
    "anywhere",
    "latam",
    "latin america",
    "colombia",
    "americas",
)
_PENALTY_TERMS: tuple[str, ...] = (
    "staff ",
    "principal",
    "director",
    "head of",
    "vp ",
    "vice president",
    "10+ years",
    "12+ years",
    "15+ years",
    "intern ",
    "internship",
)
_NOISE_TERMS: tuple[str, ...] = (
    "sales",
    "account executive",
    "marketing manager",
    "recruiter",
    "customer success",
)

_ROLE_CAP = 0.5
_DOMAIN_STEP = 0.08
_DOMAIN_CAP = 0.3
_LOCATION_BONUS = 0.12
_DREAM_BONUS = 0.15
_PENALTY = 0.2
_NOISE_PENALTY = 0.5

_BOARD_COMPANY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("boards.greenhouse.io", re.compile(r"^/([^/?#]+)")),
    ("job-boards.greenhouse.io", re.compile(r"^/([^/?#]+)")),
    ("jobs.lever.co", re.compile(r"^/([^/?#]+)")),
    ("jobs.ashbyhq.com", re.compile(r"^/([^/?#]+)")),
    ("apply.workable.com", re.compile(r"^/([^/?#]+)")),
    ("jobs.smartrecruiters.com", re.compile(r"^/([^/?#]+)")),
)
# Title gate for postings read directly from a dream company's board. The
# board lists everything (sales, legal, recruiting); only research-shaped
# individual-contributor titles become leads.
_BOARD_TITLE_INCLUDE: tuple[str, ...] = (
    "research",
    "scientist",
    "fellow",
    "machine learning",
    "reinforcement learning",
    "alignment",
    "interpretability",
    "post-training",
    "pretraining",
    "pre-training",
    "evals",
    "evaluation",
    "ml ",
    "ml/",
)
_BOARD_TITLE_EXCLUDE: tuple[str, ...] = (
    "data scientist",
    "support",
    "architect",
    "solutions",
    "infrastructure",
    "platform",
    "ads ",
    "growth",
    "account",
    "recruiter",
    "sourcer",
    "manager",
    "counsel",
    "marketing",
    "sales",
    "people ",
    "communications",
    "designer",
    "director",
    "head of",
    "lead ",
    "staff ",
    "principal",
    "senior ",
    "economist",
    "partner",
    "strategy",
    "product ",
    "operations",
    "administrator",
    "coordinator",
    "analyst",
)

_TITLE_COMPANY_RE = re.compile(
    r"^(?P<title>.+?)\s+(?:at|@|-|–|\|)\s+(?P<company>[^|\-–]+?)\s*$"
)


@dataclass
class RadarOutcome:
    query: str
    fetched: int = 0
    failed: bool = False
    error: str | None = None


Lane = Literal["realista", "ambicioso", "ambos"]

# Two leagues, one goal. "realista" is where applications go every week;
# "ambicioso" is the dream boards, one carefully prepared application a
# month. Mixing them in one list let the dream league bury the real one.
_REALISTIC_INLINE = 3
_AMBITIOUS_INLINE = 3


def parse_lane(text: str | None) -> tuple[Lane, str | None]:
    """Split "realista", "ambicioso forecasting", "ambos" into (lane, topic)."""
    raw = (text or "").strip()
    if not raw:
        return "ambos", None
    tokens = raw.split()
    head = tokens[0].lower().rstrip("s")
    lane: Lane | None = None
    if head in {"realista", "realist", "real"}:
        lane = "realista"
    elif head in {"ambicioso", "ambiciosa", "ambitious", "dream"}:
        lane = "ambicioso"
    elif head in {"ambo", "todo", "all"}:
        lane = "ambos"
    if lane is None:
        return "ambos", raw
    rest = " ".join(tokens[1:]).strip()
    return lane, rest or None


@dataclass
class RadarResult:
    new_leads: list[JobLead] = field(default_factory=list)
    lane: Lane = "ambos"
    already_known: int = 0
    below_fit: int = 0
    # Best of the discarded, so the operator can show what it judged and why.
    below_fit_samples: list[JobLeadCandidate] = field(default_factory=list)
    outcomes: list[RadarOutcome] = field(default_factory=list)

    @property
    def all_failed(self) -> bool:
        return bool(self.outcomes) and all(o.failed for o in self.outcomes)

    @property
    def realistic(self) -> list[JobLead]:
        leads = [lead for lead in self.new_leads if not lead.dream]
        return sorted(leads, key=lambda lead: lead.fit_score, reverse=True)

    @property
    def ambitious(self) -> list[JobLead]:
        leads = [lead for lead in self.new_leads if lead.dream]
        return sorted(leads, key=lambda lead: lead.fit_score, reverse=True)


def _row_to_lead(row: aiosqlite.Row) -> JobLead:
    keys = row.keys()
    remote_raw = row["remote"]
    return JobLead(
        id=int(row["id"]),
        source=str(row["source"]),
        source_id=str(row["source_id"]) if row["source_id"] is not None else None,
        title=str(row["title"]),
        company=str(row["company"]) if row["company"] is not None else None,
        url=str(row["url"]),
        location=str(row["location"]) if row["location"] is not None else None,
        remote=None if remote_raw is None else bool(remote_raw),
        summary=str(row["summary"] or ""),
        published_at=parse_stamp(row["published_at"]),
        fit_score=float(row["fit_score"] or 0.0),
        fit_note=str(row["fit_note"] or ""),
        dream=bool(row["dream"]),
        status=JobStatus(str(row["status"])),
        notes=str(row["notes"]) if row["notes"] is not None else None,
        found_at=parse_stamp(row["found_at"]) or datetime.now(UTC),
        applied_at=parse_stamp(row["applied_at"]),
        updated_at=parse_stamp(row["updated_at"]),
        details=_details_from_row(row),
        enriched_at=parse_stamp(row["enriched_at"]) if "enriched_at" in keys else None,
        has_posting_text=bool(row["posting_text"]) if "posting_text" in keys else False,
    )


def _details_from_row(row: aiosqlite.Row) -> JobPostingDetails | None:
    if "details_json" not in row.keys() or not row["details_json"]:
        return None
    try:
        return JobPostingDetails.model_validate_json(str(row["details_json"]))
    except ValueError:
        logger.warning("Malformed details_json on job lead %s.", row["id"])
        return None


def company_from_url(url: str) -> str | None:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    for board_host, pattern in _BOARD_COMPANY_PATTERNS:
        if host == board_host:
            match = pattern.match(parts.path)
            if match:
                return _company_from_slug(match.group(1))
    return None


_ACRONYM_TOKENS = {"ai", "ml", "llm", "nlp", "hq", "io", "us", "uk", "eu"}


def _company_from_slug(slug: str) -> str | None:
    """ "hippocratic%20ai" -> "Hippocratic AI", "mistral.ai" -> "Mistral AI"."""
    decoded = unquote(slug)
    tokens = [t for t in re.split(r"[\s\-_.+]+", decoded) if t]
    cleaned: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in _ACRONYM_TOKENS:
            cleaned.append(lowered.upper())
        elif any(ch.isupper() for ch in token[1:]):
            cleaned.append(token)  # already cased by the board (e.g. DeepMind)
        else:
            cleaned.append(token.capitalize())
    return " ".join(cleaned) or None


# Job-board hosts where only some paths are postings. Everything else on the
# host (people profiles, company pages, feeds) is noise for the radar.
_PATH_REQUIRED: dict[str, tuple[str, ...]] = {
    "linkedin.com": ("/jobs/",),
    "www.linkedin.com": ("/jobs/",),
    "wellfound.com": ("/jobs", "/l/"),
    "news.ycombinator.com": ("/item",),
    "www.ycombinator.com": ("/companies", "/jobs"),
}


def looks_like_job_posting(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    required = _PATH_REQUIRED.get(host)
    if required is None:
        return True
    return any(parts.path.startswith(prefix) for prefix in required)


def split_title_company(title: str) -> tuple[str, str | None]:
    match = _TITLE_COMPANY_RE.match(title.strip())
    if match is None:
        return title.strip(), None
    company = match.group("company").strip()
    if len(company) > 60 or len(company) < 2:
        return title.strip(), None
    return match.group("title").strip(), company


def is_dream_company(company: str | None) -> bool:
    if not company:
        return False
    lowered = company.lower()
    return any(target.lower() in lowered for target in settings.job_target_company_list)


def score_fit(
    *, title: str, summary: str, company: str | None
) -> tuple[float, str, bool]:
    """Deterministic fit in [0, 1] plus a note naming what matched."""
    corpus = f"{title} {title} {summary}".lower()
    notes: list[str] = []

    role_score = 0.0
    role_hits: list[str] = []
    for term, weight in _ROLE_TERMS.items():
        if term in corpus:
            role_score += weight
            role_hits.append(term)
    role_score = min(role_score, _ROLE_CAP)
    if role_hits:
        notes.append("rol: " + ", ".join(role_hits[:3]))

    domain_hits = [term for term in _DOMAIN_TERMS if term in corpus]
    domain_score = min(len(domain_hits) * _DOMAIN_STEP, _DOMAIN_CAP)
    if domain_hits:
        notes.append("dominio: " + ", ".join(domain_hits[:4]))

    location_hit = any(term in corpus for term in _LOCATION_TERMS)
    location_score = _LOCATION_BONUS if location_hit else 0.0
    if location_hit:
        notes.append("remoto o LATAM")

    dream = is_dream_company(company)
    dream_score = _DREAM_BONUS if dream else 0.0
    if dream:
        notes.append(f"empresa objetivo: {company}")

    # Seniority words count only in the title: posting bodies mention "staff"
    # and "principal" in boilerplate. Year requirements count anywhere.
    title_lower = f" {title.lower()} "
    penalty = 0.0
    penalty_hits = [
        term.strip()
        for term in _PENALTY_TERMS
        if (term in corpus if "years" in term else term in title_lower)
    ]
    if penalty_hits:
        penalty += _PENALTY
        notes.append("seniority fuera de rango: " + ", ".join(penalty_hits[:2]))
    # Same for non-technical roles: "sales" shows up in company boilerplate.
    noise_hits = [term for term in _NOISE_TERMS if term in title_lower]
    if noise_hits:
        penalty += _NOISE_PENALTY
        notes.append("no es un rol técnico")

    total = role_score + domain_score + location_score + dream_score - penalty
    total = max(0.0, min(total, 1.0))
    return round(total, 3), " · ".join(notes) or "sin coincidencias claras", dream


def board_title_passes(title: str) -> bool:
    lowered = f" {title.lower()} "
    if any(term in lowered for term in _BOARD_TITLE_EXCLUDE):
        return False
    return any(term in lowered for term in _BOARD_TITLE_INCLUDE)


def candidate_from_board(
    posting: job_boards.BoardPosting, *, company: str
) -> JobLeadCandidate | None:
    if not board_title_passes(posting.title):
        return None
    facts = " · ".join(part for part in (posting.location, posting.department) if part)
    body = trim_to_boundary(posting.text, 500)
    summary = trim_to_boundary(f"{facts}. {body}" if facts else body, 600)
    fit, note, dream = score_fit(
        title=posting.title, summary=f"{facts} {body}", company=company
    )
    return JobLeadCandidate(
        source=posting.board,
        source_id=posting.source_id,
        title=posting.title[:300],
        company=company,
        url=posting.url,
        location=(posting.location or "")[:160] or None,
        remote=posting.remote,
        summary=summary,
        published_at=posting.published_at,
        fit_score=fit,
        fit_note=note,
        dream=dream,
    )


async def _run_boards(
    db: aiosqlite.Connection, result: RadarResult, seen_urls: set[str]
) -> None:
    """Read each configured board and persist the research-shaped postings
    not seen before. Descriptions are fetched only for new Greenhouse leads."""
    for kind, slug, company in settings.job_board_source_list:
        outcome = RadarOutcome(query=f"{company} · {kind}")
        try:
            if kind == "greenhouse":
                postings = await job_boards.fetch_greenhouse(slug)
            else:
                postings = await job_boards.fetch_ashby(slug)
        except Exception as exc:
            outcome.failed = True
            outcome.error = trim_to_boundary(str(exc), 160) or exc.__class__.__name__
            result.outcomes.append(outcome)
            logger.warning("Job board %s/%s failed: %s", kind, slug, exc)
            continue

        candidates = [
            candidate
            for posting in postings
            if (candidate := candidate_from_board(posting, company=company)) is not None
        ]
        outcome.fetched = len(candidates)
        result.outcomes.append(outcome)
        by_url = {posting.url: posting for posting in postings}
        known = await known_job_lead_urls(db, [c.url for c in candidates])

        for candidate in candidates:
            if candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            if candidate.url in known:
                result.already_known += 1
                continue
            posting = by_url[candidate.url]
            text = posting.text
            if not text and posting.board == "greenhouse":
                try:
                    text = await job_boards.fetch_greenhouse_content(
                        slug, posting.source_id
                    )
                except Exception as exc:
                    logger.warning(
                        "Greenhouse content for %s failed: %s", posting.url, exc
                    )
                    text = ""
            if text:
                # Score again with the body: domain terms live there. Location
                # and department stay in front so the remote bonus still counts.
                fit, note, dream = score_fit(
                    title=candidate.title,
                    summary=f"{candidate.summary[:200]} {text[:4000]}",
                    company=company,
                )
                candidate = candidate.model_copy(
                    update={"fit_score": fit, "fit_note": note, "dream": dream}
                )
            # The company is certain here, so the fit threshold applies even
            # to dream boards: a support or ads role at OpenAI is not a lead.
            if candidate.fit_score < settings.job_min_fit:
                result.below_fit += 1
                result.below_fit_samples.append(candidate)
                continue
            lead_id, created = await _persist(db, candidate)
            if not created:
                result.already_known += 1
                continue
            if text:
                await set_job_lead_posting_text(
                    db, lead_id=lead_id, posting_text=text[:12000]
                )
            row = await get_job_lead_by_id(db, lead_id)
            if row is not None:
                result.new_leads.append(_row_to_lead(row))


def candidate_from_exa(result: exa_client.ExaResultDict) -> JobLeadCandidate | None:
    raw_title = (result.get("title") or "").strip()
    url = (result.get("url") or "").strip()
    if not raw_title or not url or not looks_like_job_posting(url):
        return None
    title, company_from_title = split_title_company(raw_title)
    company = company_from_url(url) or company_from_title
    highlights = result.get("highlights") or []
    summary = trim_to_boundary(" ".join(h for h in highlights if h), 600)
    corpus = f"{raw_title} {summary}".lower()
    remote = True if "remote" in corpus else None
    fit, note, dream = score_fit(title=title, summary=summary, company=company)
    published_raw = result.get("publishedDate")
    published_at = None
    if published_raw:
        try:
            published_at = datetime.fromisoformat(
                str(published_raw).replace("Z", "+00:00")
            )
        except ValueError:
            published_at = None
    return JobLeadCandidate(
        source="exa",
        source_id=str(result.get("id") or "") or None,
        title=title[:300],
        company=company,
        url=url,
        location=None,
        remote=remote,
        summary=summary,
        published_at=published_at,
        fit_score=fit,
        fit_note=note,
        dream=dream,
    )


async def _persist(
    db: aiosqlite.Connection, candidate: JobLeadCandidate
) -> tuple[int, bool]:
    return await insert_job_lead(
        db,
        source=candidate.source,
        source_id=candidate.source_id,
        title=candidate.title,
        company=candidate.company,
        url=candidate.url,
        location=candidate.location,
        remote=candidate.remote,
        summary=candidate.summary,
        published_at=stamp(candidate.published_at) if candidate.published_at else None,
        fit_score=candidate.fit_score,
        fit_note=candidate.fit_note,
        dream=candidate.dream,
    )


async def run_radar(
    db: aiosqlite.Connection,
    *,
    query: str | None = None,
    now: datetime | None = None,
    lane: Lane = "ambos",
) -> RadarResult:
    """Search the configured lane(s) (or one ad-hoc query) and persist new leads.

    realista  -> Exa job boards only, dream companies excluded from the list.
    ambicioso -> direct dream boards only (Anthropic, OpenAI, DeepMind, ...).
    ambos     -> both, reported as two separate sections.
    """
    moment = now or datetime.now(UTC)
    queries = (
        (query.strip(),) if query and query.strip() else settings.job_radar_query_list
    )
    since = (
        (moment - timedelta(days=max(settings.job_radar_days, 1))).date().isoformat()
    )
    domains = list(settings.job_radar_domain_list) or None
    result = RadarResult(lane=lane)
    seen_urls: set[str] = set()

    # Dream boards first; an ad-hoc `jobs <tema>` stays a pure search.
    if lane != "realista" and not (query and query.strip()):
        await _run_boards(db, result, seen_urls)

    exa_queries = queries if lane != "ambicioso" else ()
    for radar_query in exa_queries:
        outcome = RadarOutcome(query=radar_query)
        try:
            hits = await exa_client.search(
                f"{radar_query} job opening",
                num_results=10,
                include_domains=domains,
                start_published_date=since,
                with_text=True,
            )
        except Exception as exc:
            outcome.failed = True
            outcome.error = trim_to_boundary(str(exc), 160) or exc.__class__.__name__
            result.outcomes.append(outcome)
            logger.warning("Job radar query %r failed: %s", radar_query, exc)
            continue
        outcome.fetched = len(hits)
        result.outcomes.append(outcome)

        for hit in hits:
            candidate = candidate_from_exa(hit)
            if candidate is None or candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            if candidate.fit_score < settings.job_min_fit and not candidate.dream:
                result.below_fit += 1
                result.below_fit_samples.append(candidate)
                continue
            lead_id, created = await _persist(db, candidate)
            if not created:
                result.already_known += 1
                continue
            posting_text = (hit.get("text") or "").strip()
            if posting_text:
                await set_job_lead_posting_text(
                    db, lead_id=lead_id, posting_text=posting_text[:12000]
                )
            row = await get_job_lead_by_id(db, lead_id)
            if row is not None:
                result.new_leads.append(_row_to_lead(row))

    # Salary, country and requirements for the best new leads, inline. The
    # weekly cron enriches the rest so a chat command stays responsive.
    if lane == "realista":
        result.new_leads = [lead for lead in result.new_leads if not lead.dream]
    to_enrich = [lead.id for lead in result.realistic[:_REALISTIC_INLINE]] + [
        lead.id for lead in result.ambitious[:_AMBITIOUS_INLINE]
    ]
    inline_limit = max(settings.job_enrich_inline_limit, 0)
    for lead_id in to_enrich[: max(inline_limit, len(to_enrich))]:
        enriched = await enrich_lead(db, lead_id)
        if enriched is not None:
            result.new_leads = [
                enriched if lead.id == lead_id else lead for lead in result.new_leads
            ]

    result.new_leads.sort(key=lambda lead: (lead.dream, lead.fit_score), reverse=True)
    result.below_fit_samples.sort(key=lambda c: c.fit_score, reverse=True)
    del result.below_fit_samples[3:]
    logger.info(
        "Job radar: %d sources, %d new, %d known, %d below fit.",
        len(result.outcomes),
        len(result.new_leads),
        result.already_known,
        result.below_fit,
    )
    return result


async def ensure_posting_text(db: aiosqlite.Connection, lead_id: int) -> bool:
    """Recover the posting text by URL for leads stored without it.

    Returns True when text is available afterwards. Never raises: a failed
    fetch just leaves the lead as it was, and callers say so honestly.
    """
    row = await get_job_lead_by_id(db, lead_id)
    if row is None:
        return False
    keys = row.keys()
    if "posting_text" in keys and row["posting_text"]:
        return True
    url = str(row["url"])
    try:
        results = await exa_client.get_contents([url])
    except Exception as exc:
        logger.warning("Posting text recovery failed for lead %s: %s", lead_id, exc)
        return False
    for item in results:
        text = (item.get("text") or "").strip()
        if text:
            await set_job_lead_posting_text(
                db, lead_id=lead_id, posting_text=text[:12000]
            )
            return True
    return False


async def enrich_lead(db: aiosqlite.Connection, lead_id: int) -> JobLead | None:
    """Extract salary/country/requirements from the stored posting text.

    Returns the refreshed lead, or None when there is nothing to extract from
    (no posting text, no LLM) or the extractor failed. Never raises.
    """
    row = await get_job_lead_by_id(db, lead_id)
    if row is None:
        return None
    keys = row.keys()
    posting_text = str(row["posting_text"]) if "posting_text" in keys else ""
    if not posting_text.strip():
        return None
    extractor = get_job_details_extractor()
    if extractor is None:
        return None
    try:
        details = await extractor.generate(
            title=str(row["title"]), url=str(row["url"]), text=posting_text
        )
    except Exception as exc:  # belt-and-suspenders: never break a radar run
        logger.warning("Job details extraction raised for lead %s: %s", lead_id, exc)
        return None
    if details is None:
        return None

    location = ", ".join(part for part in (details.city, details.country) if part)
    await update_job_lead_details(
        db,
        lead_id=lead_id,
        details_json=details.model_dump_json(),
        salary_text=details.salary_text,
        salary_min_usd_year=details.salary_min_usd_year,
        salary_max_usd_year=details.salary_max_usd_year,
        country=details.country,
        remote_policy=details.remote_policy,
        location=location or None,
        company=details.company,
        enriched_at=stamp(datetime.now(UTC)),
    )
    return await get_lead(db, lead_id)


async def enrich_pending(db: aiosqlite.Connection, *, limit: int = 20) -> int:
    """Enrich leads that still lack details. Used by the weekly cron."""
    rows = await list_job_leads_pending_enrichment(db, limit=limit)
    done = 0
    for row in rows:
        if await enrich_lead(db, int(row["id"])) is not None:
            done += 1
    return done


def salary_verdict(lead: JobLead) -> str | None:
    """Compare the stated USD salary with the goal. None when not comparable."""
    details = lead.details
    if details is None:
        return None
    target = settings.target_salary_usd_year
    reference = details.salary_max_usd_year or details.salary_min_usd_year
    if reference is None:
        return None
    if reference >= target:
        return "por encima de tu meta"
    return "por debajo de tu meta"


async def get_lead(db: aiosqlite.Connection, lead_id: int) -> JobLead:
    row = await get_job_lead_by_id(db, lead_id)
    if row is None:
        raise LookupError(f"Job lead {lead_id} not found.")
    return _row_to_lead(row)


async def mark_status(
    db: aiosqlite.Connection,
    *,
    lead_id: int,
    status: JobStatus,
    note: str | None = None,
    now: datetime | None = None,
) -> JobLead:
    await get_lead(db, lead_id)
    applied_at = (
        stamp(now or datetime.now(UTC)) if status is JobStatus.APPLIED else None
    )
    await update_job_lead_status(
        db,
        lead_id=lead_id,
        status=status.value,
        notes=note.strip() if note and note.strip() else None,
        applied_at=applied_at,
    )
    return await get_lead(db, lead_id)


async def pipeline(db: aiosqlite.Connection) -> dict[JobStatus, list[JobLead]]:
    rows = await list_job_leads(
        db, statuses=tuple(s.value for s in ACTIVE_STATUSES), limit=40
    )
    grouped: dict[JobStatus, list[JobLead]] = {status: [] for status in ACTIVE_STATUSES}
    for row in rows:
        lead = _row_to_lead(row)
        grouped[lead.status].append(lead)
    return grouped


async def status_counts(db: aiosqlite.Connection) -> dict[str, int]:
    return await count_job_leads_by_status(db)


_STATUS_WORDS: dict[str, JobStatus] = {
    "aplicado": JobStatus.APPLIED,
    "aplicada": JobStatus.APPLIED,
    "applied": JobStatus.APPLIED,
    "entrevista": JobStatus.INTERVIEW,
    "interview": JobStatus.INTERVIEW,
    "oferta": JobStatus.OFFER,
    "offer": JobStatus.OFFER,
    "rechazado": JobStatus.REJECTED,
    "rechazada": JobStatus.REJECTED,
    "rejected": JobStatus.REJECTED,
    "guardado": JobStatus.SAVED,
    "guardada": JobStatus.SAVED,
    "guardar": JobStatus.SAVED,
    "saved": JobStatus.SAVED,
    "descartado": JobStatus.DISMISSED,
    "descartada": JobStatus.DISMISSED,
    "descartar": JobStatus.DISMISSED,
    "dismissed": JobStatus.DISMISSED,
}


def parse_status_word(word: str) -> JobStatus | None:
    return _STATUS_WORDS.get(word.strip().lower())
