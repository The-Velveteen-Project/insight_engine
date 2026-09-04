"""
Prompt for the project brief of one campaign build item (Phase 3.5).
"""

from __future__ import annotations

PROJECT_SYSTEM_PROMPT = """
You write a project brief that Carlos will hand to Claude (Claude Code or a
Claude Project) to build one item of his monthly plan. The brief must let
Claude work for days without asking basic questions, and it must make
Claude research before it builds.

Rules:
- `objective`: what will exist at the end, for whom, and why it matters for
  the target role. Concrete nouns. No hype.
- `closes_gap`: the gap item (from the analysis) this build answers, quoted
  or paraphrased, and how an employer would see it closed.
- `out_of_scope`: what the build deliberately does not do (2–5 items).
- `inputs_needed`: files, keys, datasets or decisions Claude must get from
  Carlos before starting (repo access, data paths, the CV, the gap text).
- `stages`: 4 to 6. The first stage is always deep research. For every
  stage give `deep_research` instructions as concrete search tasks: what to
  look for, where (papers, official specs, reference repos, benchmark
  conventions), and what to extract into notes before writing code.
  Deliverables are files or artifacts; acceptance checks are testable
  statements (a command that passes, a number reported, a doc that exists).
- `constraints`: Velveteen build rules apply: deterministic core, LLMs only
  transform structured inputs into structured outputs, tests with pytest,
  ruff and mypy strict, a README that reads as an instrument, a model or
  data card when data is involved, no invented results, public repo.
- `post_claim`: the one-sentence claim the finished build lets Carlos defend
  in a LinkedIn post, with the number it should be able to cite.
- `kickoff_prompt`: the exact text Carlos pastes into Claude to start. It
  must include: who Carlos is (two lines), the target role and gap item,
  the objective, the stage list with the instruction to complete the
  research stage and report findings before writing code, the constraints,
  and how to report progress (a short note per stage with what was verified).
  English. 250–600 words.
- Never invent facts about Carlos, his data, or results. Use only the
  inputs provided. Spanish for the brief fields except `kickoff_prompt`.
""".strip()


def build_project_prompt(
    *,
    item_title: str,
    item_why: str,
    campaign_thesis: str,
    lead_title: str,
    company: str | None,
    gap_text: str,
    master_summary: str,
    velveteen_context: str,
) -> str:
    return (
        f"Plan item to build: {item_title}\n"
        f"Why it is in the plan: {item_why}\n"
        f"Month thesis: {campaign_thesis}\n"
        f"Target role: {lead_title} at {company or 'the company'}\n\n"
        f"GAP ANALYSIS:\n{gap_text}\n\n"
        f"MASTER CV (summary):\n{master_summary}\n\n"
        f"VELVETEEN BUILD CONTEXT:\n{velveteen_context}\n\n"
        "Write the project brief."
    )
