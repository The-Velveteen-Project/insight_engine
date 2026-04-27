"""
LinkedIn shipping mode schemas (Sub-phase B.5).

Two surfaces, one input:
- LinkedInPost: a paste-ready post the operator delivers in Telegram.
- LinkedInPromptKit: a portable prompt + brand context for Carlos to feed
  into another LLM (Claude/GPT) when he wants to iterate himself.

Both are produced from a persisted EditorialPlan. The operator does not
publish to LinkedIn for him — the LinkedIn API is hostile to automated
posting, and the manual paste path is the right shape at this scale.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.editorial import EditorialSignalContext, RecommendedAction


class LinkedInPost(BaseModel):
    """A post ready to be copied into LinkedIn unchanged.

    Length targets are LinkedIn-specific:
    - hook ≤ 200 chars so it survives the "...ver más" cut on mobile
    - each body paragraph stays scannable on a phone screen
    - total assembled length lands in the 1000-1500 char sweet spot

    Hard rules baked into the prompt: no decorative emojis, no marketing
    superlatives, no "déjame saber" generic CTAs. Spanish primary, with
    English allowed only for technical terms used as-is.
    """

    hook: str = Field(min_length=20, max_length=240)
    body_paragraphs: list[str] = Field(min_length=2, max_length=6)
    closing: str = Field(min_length=10, max_length=320)
    hashtags: list[str] = Field(min_length=0, max_length=6)


class LinkedInPostInput(BaseModel):
    """Structured context for the LinkedIn writer.

    Stays close to what the editorial planner already produced (action,
    angle, why_it_matters, signals) so the post is grounded in the same
    reading the operator already showed Carlos in the plan summary.
    """

    plan_id: int
    recommended_action: RecommendedAction
    angle: str = Field(min_length=8, max_length=240)
    why_it_matters: str = Field(min_length=12, max_length=600)
    portfolio_value: str = Field(min_length=12, max_length=320)
    draft_hook: str = Field(min_length=8, max_length=180)
    draft_points: list[str] = Field(min_length=2, max_length=4)
    draft_closing: str = Field(min_length=8, max_length=180)
    signals: list[EditorialSignalContext] = Field(min_length=1, max_length=3)
    active_goal: str | None = Field(default=None, max_length=400)


class LinkedInPromptKit(BaseModel):
    """Portable kit Carlos can paste into any other LLM to iterate.

    Splits the prompt into named sections so he can edit them in place
    (loosen tone, change CTA, drop a constraint) without re-deriving the
    whole context from scratch every time.
    """

    plan_id: int
    system_prompt: str = Field(min_length=80, max_length=4000)
    user_prompt: str = Field(min_length=80, max_length=6000)
    one_line_paste_command: str = Field(min_length=20, max_length=320)
