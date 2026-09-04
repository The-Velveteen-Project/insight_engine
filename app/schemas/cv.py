"""
Gap analysis and tailored CV schemas (Phase 2.6).

Both are produced from the master CV plus one job posting. The master CV is
the only source of facts; the model reorders, selects and rephrases, and
must say what it left out.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Length caps are generous on purpose: models cut text mid-word to satisfy a
# tight maxLength, and a clipped sentence is worse than a long one.
class RequirementEvidence(BaseModel):
    requirement: str = Field(min_length=3, max_length=240)
    evidence: str = Field(min_length=3, max_length=480)
    strength: Literal["strong", "partial", "weak"]


class GapAnalysis(BaseModel):
    verdict: Literal["apply_now", "apply_with_tailoring", "stretch", "skip"]
    verdict_reason: str = Field(min_length=20, max_length=800)
    covered: list[RequirementEvidence] = Field(default_factory=list, max_length=8)
    missing: list[str] = Field(default_factory=list, max_length=6)
    foreground: list[str] = Field(min_length=1, max_length=4)
    keywords_to_mirror: list[str] = Field(default_factory=list, max_length=8)
    opener: str = Field(min_length=20, max_length=700)


class CVEntry(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    subtitle: str | None = Field(default=None, max_length=200)
    bullets: list[str] = Field(min_length=1, max_length=4)


class TailoredCV(BaseModel):
    headline: str = Field(min_length=10, max_length=160)
    summary: str = Field(min_length=80, max_length=900)
    highlighted_projects: list[CVEntry] = Field(min_length=1, max_length=4)
    experience: list[CVEntry] = Field(min_length=1, max_length=4)
    education: list[str] = Field(min_length=1, max_length=5)
    publications: list[str] = Field(default_factory=list, max_length=4)
    distinctions: list[str] = Field(default_factory=list, max_length=5)
    skills: list[str] = Field(min_length=1, max_length=6)
    tailoring_notes: str = Field(min_length=20, max_length=1400)

    def render_markdown(self, *, identity_block: str) -> str:
        """One-page Markdown CV. Identity comes from the master, never the model."""
        lines: list[str] = [
            identity_block.strip(),
            "",
            f"**{self.headline.strip()}**",
            "",
        ]
        lines.append(self.summary.strip())
        lines.append("")
        lines.append("## Selected projects")
        for entry in self.highlighted_projects:
            lines.extend(_render_entry(entry))
        lines.append("## Experience")
        for entry in self.experience:
            lines.extend(_render_entry(entry))
        lines.append("## Education")
        lines.extend(f"- {item.strip()}" for item in self.education)
        lines.append("")
        if self.publications:
            lines.append("## Publications and manuscripts")
            lines.extend(f"- {item.strip()}" for item in self.publications)
            lines.append("")
        if self.distinctions:
            lines.append("## Fellowships and awards")
            lines.extend(f"- {item.strip()}" for item in self.distinctions)
            lines.append("")
        lines.append("## Skills")
        lines.extend(f"- {item.strip()}" for item in self.skills)
        return "\n".join(lines).strip() + "\n"


def _render_entry(entry: CVEntry) -> list[str]:
    heading = f"**{entry.title.strip()}**"
    if entry.subtitle:
        heading += f" · {entry.subtitle.strip()}"
    lines = [heading]
    lines.extend(f"- {bullet.strip()}" for bullet in entry.bullets)
    lines.append("")
    return lines
