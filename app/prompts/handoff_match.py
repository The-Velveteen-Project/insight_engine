"""
Prompt for the handoff follow-up matcher (Sub-phase B).

Two days after Carlos says "después" to a proactive MVP handoff offer, the
operator scans his priority repos to see whether a new repo (or recent activity
on an existing one) already addresses the plan's angle. The judgment is small
and structured: match yes/no, which repo, and a one-sentence rationale.
"""

from __future__ import annotations

from app.schemas.goals import HandoffMatchInput
from app.services.context_hub import get_static_context

_SHARED_CONTEXT = get_static_context()

HANDOFF_MATCH_SYSTEM_PROMPT = f"""
{_SHARED_CONTEXT}

## Handoff follow-up matcher
You compare an editorial plan that proposed a small applied build against
the user's priority GitHub repositories. Your only job is to decide whether
any repo already meaningfully addresses the plan's angle.

Rules:
- Be conservative. If no repo clearly maps to the plan's angle, set
  match=false. A vague topical overlap is not a match.
- A match should be defensible from the repo's name, description or recent
  activity summary — never invent activity that is not in the input.
- When match=true, set repo_full_name to the exact `full_name` from the
  input list, and write a single short rationale sentence in second person
  to Carlos explaining why this repo seems aligned.
- When match=false, leave repo_full_name as null and rationale empty.
- confidence is a float between 0.0 and 1.0 reflecting how sure you are.
- The output must fit the supplied schema and contain no extra fields.
""".strip()


def build_handoff_match_prompt(context: HandoffMatchInput) -> str:
    repo_lines: list[str] = []
    for repo in context.repos:
        repo_lines.append(
            "\n".join(
                [
                    f"full_name: {repo.full_name}",
                    f"description: {repo.description or ''}",
                    f"last_activity: {repo.last_activity_summary or ''}",
                ]
            )
        )
    joined_repos = "\n\n".join(repo_lines)
    titles = "\n".join(f"- {title}" for title in context.signal_titles)

    return (
        f"Plan angle: {context.plan_angle}\n"
        f"Plan why-it-matters: {context.plan_why}\n"
        "Signals that triggered the plan:\n"
        f"{titles}\n\n"
        "Candidate repositories:\n"
        f"{joined_repos}\n\n"
        "Decide whether any candidate repo already meaningfully addresses "
        "the plan's angle. Output only the structured judgment."
    )
