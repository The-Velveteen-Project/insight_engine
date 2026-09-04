"""
Project brief for one campaign build item (Phase 3.5).

The brief is what Carlos hands to Claude (Code or Projects) to run the
build: stages with deep-research instructions, deliverables, acceptance
checks, and a kickoff prompt. It is a document, not an executable job.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectStage(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    goal: str = Field(min_length=10, max_length=480)
    deep_research: list[str] = Field(default_factory=list, max_length=6)
    deliverables: list[str] = Field(min_length=1, max_length=6)
    acceptance: list[str] = Field(min_length=1, max_length=6)


class ProjectBrief(BaseModel):
    title: str = Field(min_length=6, max_length=160)
    objective: str = Field(min_length=40, max_length=900)
    closes_gap: str = Field(min_length=10, max_length=400)
    out_of_scope: list[str] = Field(default_factory=list, max_length=6)
    inputs_needed: list[str] = Field(default_factory=list, max_length=8)
    stages: list[ProjectStage] = Field(min_length=3, max_length=6)
    constraints: list[str] = Field(min_length=2, max_length=8)
    kickoff_prompt: str = Field(min_length=200, max_length=6000)
    post_claim: str = Field(min_length=20, max_length=400)

    def render_markdown(self) -> str:
        lines: list[str] = [f"# {self.title.strip()}", ""]
        lines += ["## Objetivo", self.objective.strip(), ""]
        lines += ["## Qué brecha cierra", self.closes_gap.strip(), ""]
        if self.out_of_scope:
            lines += (
                ["## Fuera de alcance"] + [f"- {x}" for x in self.out_of_scope] + [""]
            )
        if self.inputs_needed:
            lines += ["## Insumos que Claude necesita"]
            lines += [f"- {x}" for x in self.inputs_needed] + [""]
        lines += ["## Etapas", ""]
        for index, stage in enumerate(self.stages, start=1):
            lines.append(f"### Etapa {index}: {stage.name.strip()}")
            lines.append(stage.goal.strip())
            if stage.deep_research:
                lines.append("")
                lines.append("Investigación profunda antes de construir:")
                lines += [f"- {x}" for x in stage.deep_research]
            lines.append("")
            lines.append("Entregables:")
            lines += [f"- {x}" for x in stage.deliverables]
            lines.append("")
            lines.append("Criterios de aceptación:")
            lines += [f"- {x}" for x in stage.acceptance]
            lines.append("")
        lines += ["## Restricciones"] + [f"- {x}" for x in self.constraints] + [""]
        lines += [
            "## El post que este build debe permitir",
            self.post_claim.strip(),
            "",
        ]
        lines += [
            "## Prompt de arranque para Claude",
            "",
            "```",
            self.kickoff_prompt.strip(),
            "```",
            "",
        ]
        return "\n".join(lines)
