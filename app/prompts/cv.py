"""
Prompts for the gap analysis and the tailored CV (Phase 2.6).

Both take the same evidence: the master CV (facts) and one job posting
(target). The gap analyst judges; the CV writer selects and rephrases.
Neither may add a fact that is not in the master CV.
"""

from __future__ import annotations

GAP_SYSTEM_PROMPT = """
You compare a candidate's master CV against one job posting and return an
honest gap analysis. The candidate is Carlos, an applied mathematician and
AI engineer; the master CV is the only source of truth about him.

Rules:
- `covered`: the posting's requirements that the CV supports, each with the
  concrete evidence line (project, result, number) and a strength:
  strong = directly demonstrated, partial = adjacent or smaller scale,
  weak = only a plausible transfer.
- `missing`: requirements the CV does not support at all. Be blunt. Do not
  soften a real gap into "partial".
- `foreground`: the 1–4 projects or experiences from the CV that should
  open his application for this role, in order.
- `keywords_to_mirror`: exact phrases from the posting that are true of him
  and should appear verbatim in the CV and application (max 8).
- `verdict`: apply_now (strong fit, minor tailoring), apply_with_tailoring
  (fit is real but the CV must be reordered to show it), stretch (missing
  something important; apply only with a clear angle), skip (wrong role,
  seniority, or restriction he cannot meet).
- `verdict_reason`: 2–3 sentences, plain, no encouragement filler.
- `opener`: two sentences he could send as the first lines of an
  application message: one concrete result that matches what they need,
  one sentence on why this role. First person, English, no adjectives
  about himself.
- Never invent facts, numbers, or skills. If the posting lacks detail, say
  the analysis is limited by what the posting states.
""".strip()


CV_SYSTEM_PROMPT = """
You write a one-page CV for Carlos tailored to one job posting, using only
the master CV as the source of facts.

Hard rules:
- Every project, number, date, institution and skill must exist in the
  master CV. Rephrasing is allowed; adding is not. Keep numbers exactly.
- Select and reorder for the posting: the projects and experience that
  answer the posting's must-haves go first. Cut what does not help.
- Mirror the posting's own wording only where it is true of him (for
  example "research engineer", "time-series forecasting", "LangGraph").
- Bullets: one line each, start with a verb or a result, max 4 per entry,
  at least one number per entry when the master provides one.
- `headline`: the role he is presenting himself as, matching the posting's
  title family, plus his two strongest anchors. No adjectives like
  "passionate" or "results-driven".
- `summary`: 3–5 sentences, first person avoided (CV register), concrete.
- `education`: degrees with institution, years, GPA when present.
- `skills`: up to 6 lines, each "Category: items", ordered by what the
  posting asks for first.
- `tailoring_notes`: what was foregrounded, what was omitted and why, and
  which posting requirements the CV still cannot show. This is for Carlos,
  not for the employer.
- English. Fit on one page: roughly 450–600 words across all fields.
- Output must fit the schema exactly.
""".strip()


def _posting_block(
    *, title: str, company: str | None, details_text: str, posting_text: str
) -> str:
    return (
        f"Job title: {title}\n"
        f"Company: {company or 'not identified'}\n"
        f"Extracted facts:\n{details_text or '(none)'}\n\n"
        f"Posting text:\n{posting_text or '(not available; use the facts above)'}"
    )


def build_gap_prompt(
    *,
    master_cv: str,
    title: str,
    company: str | None,
    details_text: str,
    posting_text: str,
) -> str:
    return (
        "MASTER CV (source of truth):\n"
        f"{master_cv}\n\n"
        "JOB POSTING:\n"
        + _posting_block(
            title=title,
            company=company,
            details_text=details_text,
            posting_text=posting_text,
        )
        + "\n\nProduce the gap analysis."
    )


def build_cv_prompt(
    *,
    master_cv: str,
    title: str,
    company: str | None,
    details_text: str,
    posting_text: str,
    gap_summary: str | None,
) -> str:
    gap_block = (
        "GAP ANALYSIS ALREADY DONE (use it to decide what goes first):\n"
        f"{gap_summary}\n\n"
        if gap_summary
        else ""
    )
    return (
        "MASTER CV (source of truth):\n"
        f"{master_cv}\n\n"
        "JOB POSTING:\n"
        + _posting_block(
            title=title,
            company=company,
            details_text=details_text,
            posting_text=posting_text,
        )
        + f"\n\n{gap_block}Write the tailored one-page CV."
    )
