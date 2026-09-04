"""
Prompt for the monthly campaign planner (Phase 3).

Input: the gap analysis for one ambitious lead plus the master CV summary.
Output: a four-week plan whose every item closes a named gap or makes
existing evidence visible. The model plans; Carlos executes; code tracks.
"""

from __future__ import annotations

CAMPAIGN_SYSTEM_PROMPT = """
You turn a job gap analysis into a four-week plan for Carlos, an applied
mathematician and AI engineer who publishes on LinkedIn and ships public
repos. The plan must be small enough to finish while he applies to other
jobs and teaches, and concrete enough that each item is either done or not.

Rules:
- 3 to 8 items total. At most 3 `build` items, each finishable in two weeks
  by one person, each with a public repo or a public artifact as its output.
- 2 to 4 `post` items, each tied to a build or to existing work in the CV,
  each described as the claim the post will defend (not "write a post").
- `learn` items only when a hard requirement is knowledge he lacks; cap 1.
- Exactly one `apply` item in week 4: fresh gap analysis, tailored CV,
  application sent.
- `why`: name the gap item (from `missing` or a `partial` covered item) the
  work closes, or the covered strength it makes visible. Never "good to have".
- `week`: 1 to 4. Builds start in week 1; posts follow their builds.
- `thesis`: one or two sentences, what the month proves to this employer.
- Titles in Spanish with technical terms in English. Concrete nouns:
  "Servidor MCP sobre los pipelines de AntigenLM (repo público)", not
  "mejorar habilidades de agentes".
- Never invent facts about Carlos; use only the master CV summary and the
  gap analysis. Output must fit the schema exactly.
""".strip()


def build_campaign_prompt(
    *,
    lead_title: str,
    company: str | None,
    gap_text: str,
    master_summary: str,
) -> str:
    return (
        f"Target role: {lead_title} at {company or 'the company'}\n\n"
        f"GAP ANALYSIS:\n{gap_text}\n\n"
        f"MASTER CV (summary):\n{master_summary}\n\n"
        "Produce the four-week plan."
    )
