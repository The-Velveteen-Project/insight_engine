"""
Schemas for conservative MVP handoff packs.

These packs are not executable jobs. They are structured prompt bundles for:
- a prompt architect model
- a builder model such as Codex or Antigravity
- an auditor model that reviews the produced MVP
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MvpPromptBundle(BaseModel):
    system_prompt: str = Field(min_length=20)
    user_prompt: str = Field(min_length=40)


class MvpHandoffPack(BaseModel):
    plan_id: int
    signal_ids: list[int] = Field(min_length=1, max_length=3)
    thesis: str = Field(min_length=8, max_length=500)
    scope_summary: str = Field(min_length=20, max_length=500)
    builder_target: str = "codex-or-antigravity"
    auditor_target: str = "code-auditor-model"
    context_basis: list[str] = Field(min_length=2, max_length=8)
    prompt_architect: MvpPromptBundle
    builder: MvpPromptBundle
    auditor: MvpPromptBundle
