"""
Schemas for Telegram command parsing and orchestration outputs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.editorial import RecommendedAction


class CommandName(StrEnum):
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


class WeeklySummary(BaseModel):
    query: str
    top_signals: list[SignalSuggestion] = Field(min_length=1, max_length=3)
    editorial_action: RecommendedAction
    editorial_angle: str
    mvp_action: RecommendedAction
    mvp_summary: str
    next_step: str


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
