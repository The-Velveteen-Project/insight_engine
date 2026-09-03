"""
Prompt for extracting structured facts from a job posting (Phase 2.5).

The model transforms unstructured posting text into JobPostingDetails. It
never judges fit and never invents: anything the posting does not state
stays null. Fit remains deterministic in job_radar.
"""

from __future__ import annotations

JOB_DETAILS_SYSTEM_PROMPT = """
You extract facts from a job posting into a fixed schema.

Rules:
- Use only what the posting text states. Anything not stated is null or an
  empty list. Never guess a salary, a country, or years of experience.
- salary: copy the stated range into `salary_text` as written (e.g. "$150K – $200K",
  "€70.000–90.000", "COP 12M/mes"). Fill `salary_min_usd_year` and
  `salary_max_usd_year` only when the currency is USD; convert monthly to
  yearly (×12) and hourly to yearly (×2080). Leave both null for other currencies.
- country: the country where the role is based. If it says "remote (US only)"
  the country is "United States" and `location_restriction` explains the limit.
  If it is remote worldwide, country is null and remote_policy is "remote".
- remote_policy: "remote", "hybrid", "onsite", or "unknown".
- must_have: up to 8 short phrases with the hard requirements (degree, years,
  languages, frameworks, domains). nice_to_have: up to 6 preferred items.
- seniority: the level as the posting names it (e.g. "Senior", "Staff", "Early
  career"), or null.
- one_line: what the job actually is, in one plain sentence, max 200 chars.
- Output must fit the schema exactly.
""".strip()


def build_job_details_prompt(*, title: str, url: str, text: str) -> str:
    return f"Title: {title}\nURL: {url}\n\nPosting text:\n{text}"
