"""
Schemas for Telegram command parsing and orchestration outputs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.editorial import RecommendedAction


class CommandName(StrEnum):
    START = "start"
    PAPERS = "papers"
    NEWS = "news"
    SIGNALS = "signals"
    GITHUB_INSIGHTS = "github_insights"
    WEEKLY = "weekly"
    MVP_IDEAS = "mvp_ideas"
    PLAN = "plan"
    APPROVE = "approve"
    DISCARD_PLAN = "discard_plan"
    DRAFT = "draft"
    SHOW_PLAN = "show_plan"
    SHOW_DRAFT = "show_draft"
    MVP_HANDOFF = "mvp_handoff"
    GOAL = "goal"
    CLEAR_GOAL = "clear_goal"
    LINKEDIN = "linkedin"
    LINKEDIN_PROMPT = "linkedin_prompt"
    EXPLAIN_SIGNALS = "explain_signals"
    HELP = "help"
    UNKNOWN = "unknown"


class ParsedTelegramCommand(BaseModel):
    name: CommandName
    query: str | None = None
    raw_text: str


class SignalSuggestion(BaseModel):
    signal_id: int | None = None
    title: str
    why_it_matters: str
    suggested_action: RecommendedAction
    relevance_score: float
    source_label: str | None = None
    url: str | None = None


class WeeklySourceStats(BaseModel):
    """Per-source discovery diagnostics surfaced in the weekly footer.

    The weekly is trustworthy only if you can see what it tried. This is the
    minimum honest report: how many candidates each source returned, how
    many made the brief, and whether the source failed outright. The tracks
    DLC (planned) reads from the same field.
    """

    source_label: str = Field(min_length=1, max_length=80)
    candidates_returned: int = 0
    candidates_in_brief: int = 0
    failed: bool = False
    note: str | None = Field(default=None, max_length=200)


class WeeklySummary(BaseModel):
    query: str
    top_signals: list[SignalSuggestion] = Field(min_length=1, max_length=3)
    editorial_action: RecommendedAction
    editorial_angle: str
    mvp_action: RecommendedAction
    mvp_summary: str
    next_step: str
    thesis_paragraph: str | None = None
    handoff_proposal: str | None = None
    signals_evaluated: int = 0
    focus_label: str | None = None
    active_goal: str | None = None
    llm_thesis_used: bool = False
    source_stats: list[WeeklySourceStats] = Field(default_factory=list)


class MvpIdeaSuggestion(BaseModel):
    query: str
    recommended_action: RecommendedAction
    thesis: str
    problem: str
    why_it_matters: str
    possible_sources: list[str] = Field(min_length=1, max_length=5)
    system_type: str
    portfolio_fit: str
    signal_ids: list[int] = Field(default_factory=list, max_length=3)
    supporting_signals: list[SignalSuggestion] = Field(
        default_factory=list,
        max_length=3,
    )
